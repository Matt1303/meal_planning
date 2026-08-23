from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, cast

from pyomo.environ import SolverFactory
from sqlalchemy import Engine

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName
from meal_planner.optimize.data import (
    PreparedData,
    filter_recipes,
    load_inputs,
    prepare,
)
from meal_planner.optimize.model import ModelOptions, build_model, total_slack, variable_count
from meal_planner.optimize.warmstart import (
    apply_mip_start,
    build_start_values,
    load_previous_plan,
)

log = get_logger(__name__)


class RelaxationLevel(IntEnum):
    STRICT = 0
    DROP_DAILY_NUTRITION = 1
    DROP_WEEKLY_GROUPS = 2
    DROP_GROUP_TARGETS = 3


_LEVELS: list[tuple[RelaxationLevel, ModelOptions]] = [
    (
        RelaxationLevel.STRICT,
        ModelOptions(
            enforce_daily_kcal=True,
            enforce_daily_fiber=True,
            enforce_daily_protein=True,
            enforce_weekly_kcal=True,
            enforce_weekly_fiber=True,
            enforce_weekly_protein=True,
            enforce_group_targets=True,
            enforce_weekly_groups=True,
        ),
    ),
    (
        RelaxationLevel.DROP_DAILY_NUTRITION,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_daily_protein=False,
            enforce_weekly_kcal=True,
            enforce_weekly_fiber=True,
            enforce_weekly_protein=True,
            enforce_group_targets=True,
            enforce_weekly_groups=True,
        ),
    ),
    (
        RelaxationLevel.DROP_WEEKLY_GROUPS,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_daily_protein=False,
            enforce_weekly_kcal=False,
            enforce_weekly_fiber=False,
            enforce_weekly_protein=False,
            enforce_group_targets=True,
            enforce_weekly_groups=False,
        ),
    ),
    (
        RelaxationLevel.DROP_GROUP_TARGETS,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_daily_protein=False,
            enforce_weekly_kcal=False,
            enforce_weekly_fiber=False,
            enforce_weekly_protein=False,
            enforce_group_targets=False,
            enforce_weekly_groups=False,
            enforce_leftover_pairing=False,
        ),
    ),
]


PlanCell = dict[str, int | None]


@dataclass(frozen=True)
class OptimizeResult:
    """Selected plan. plan[day][meal_type] maps profile_name -> recipe_id.

    Profile name 'shared' (or rather: the literal string from ProfileSpec.name when the
    meal type is in shared_meal_types) is used for shared slots.
    """

    plan: dict[int, dict[str, PlanCell]]
    solver_status: str
    solver_seconds: float
    slack_total: float
    relaxation_level: int
    prepared: PreparedData
    # (profile_name, day) -> whey scoops the solver allocated.
    whey: dict[tuple[str, int], float] = field(default_factory=dict)
    # (profile_name, day, shared meal_type) -> servings of that dish the person
    # eats. Absent means the full serving (1.0).
    portions: dict[tuple[str, int, str], float] = field(default_factory=dict)


SHARED_KEY = "__shared__"


def optimize_plan(settings: Settings, *, engine: Engine | None = None) -> OptimizeResult:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    inputs = load_inputs(eng, include_non_plant=settings.optimizer.include_non_plant)
    if inputs.recipes.empty:
        raise RuntimeError("no recipes found")
    filtered = filter_recipes(inputs, min_rating=settings.optimizer.min_rating, settings=settings)
    if filtered.recipes.empty:
        raise RuntimeError("no recipes meet filtering criteria")
    prepared = prepare(filtered, settings)
    if not prepared.recipes:
        raise RuntimeError("no recipes left after preparation")

    settings = _maybe_force_snack_optional(settings, filtered.meal_types, prepared.recipes)

    missing_groups = [g for g in settings.daily_dozen_targets if g not in settings.portion_sizes]

    with eng.begin() as conn:
        record_metric(
            conn,
            MetricName.PORTION_SIZE_MISSING_GROUPS,
            len(missing_groups),
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.SOLVER_VARIABLE_COUNT,
            variable_count(prepared),
            correlation_id=correlation_id,
        )

    solver_name = _resolve_solver_name(settings.optimizer.solver)

    previous = load_previous_plan(eng) if settings.optimizer.warm_start else None

    def start_values(model: Any) -> dict[int, tuple[Any, float]] | None:
        if previous is None:
            return None
        values = build_start_values(
            model, prepared, previous, float(settings.topup.min_whey_scoops)
        )
        if values is None:
            log.info("optimize.warmstart_skipped", reason="previous plan no longer fits")
        return values

    last_error: str = "not attempted"
    for level, options in _LEVELS:
        log.info("optimize.attempt", relaxation=level.name, solver=solver_name)
        model = build_model(prepared, settings, options)
        start = time.time()
        try:
            condition, loaded, mip_gap = _solve_model(
                model, solver_name, settings, start_values=start_values(model)
            )
        except Exception as exc:
            last_error = str(exc)
            log.warning("optimize.solver_error", relaxation=level.name, error=last_error)
            continue
        seconds = time.time() - start
        # The nutrition constraints are soft (slack), so any level is feasible in
        # principle. Accept whatever the solver loaded as long as it is a complete
        # plan — including a time-limited incumbent — rather than relaxing the
        # calorie/protein targets away.
        plan = _extract_plan(model, prepared) if loaded else {}
        take = loaded and _plan_complete(plan, prepared)
        if take:
            whey = _extract_whey(model, prepared)
            portions = _extract_portions(model, prepared, plan)
            slack = total_slack(model, prepared)
            with eng.begin() as conn:
                record_metric(
                    conn,
                    MetricName.OPTIMIZE_RELAXATION_LEVEL,
                    int(level),
                    correlation_id=correlation_id,
                )
                record_metric(
                    conn,
                    MetricName.SOLVER_SECONDS,
                    seconds,
                    correlation_id=correlation_id,
                )
                record_metric(
                    conn,
                    MetricName.SLACK_TOTAL,
                    slack,
                    correlation_id=correlation_id,
                )
            log.info(
                "optimize.solved",
                relaxation=level.name,
                seconds=seconds,
                slack_total=slack,
                condition=condition,
                mip_gap=round(mip_gap, 4) if mip_gap is not None else None,
            )
            return OptimizeResult(
                plan=plan,
                solver_status=str(condition),
                solver_seconds=seconds,
                slack_total=slack,
                relaxation_level=int(level),
                prepared=prepared,
                whey=whey,
                portions=portions,
            )
        last_error = str(condition)
        log.warning("optimize.rejected", relaxation=level.name, condition=last_error)

    raise RuntimeError(f"all relaxation levels failed: {last_error}")


def _maybe_force_snack_optional(
    settings: Settings, meal_types_df: object, recipes: list[int]
) -> Settings:
    if "snack" not in settings.meal_types or settings.optimizer.snack_optional:
        return settings
    import pandas as pd

    df = meal_types_df  # pandas DataFrame
    if not isinstance(df, pd.DataFrame) or df.empty:
        return settings
    snack_recipes = df[(df["meal_type"] == "snack") & (df["recipe_id"].isin(recipes))]
    if snack_recipes.empty:
        log.warning(
            "optimize.no_snack_recipes_forcing_optional",
            meal_types=settings.meal_types,
        )
        return settings.model_copy(
            update={"optimizer": settings.optimizer.model_copy(update={"snack_optional": True})}
        )
    return settings


def _resolve_solver_name(preferred: str) -> str:
    """Use the preferred MILP backend if installed, else fall back to glpk."""
    try:
        if SolverFactory(preferred).available(False):
            return preferred
    except Exception:
        pass
    log.warning("optimize.solver_unavailable", preferred=preferred, fallback="glpk")
    return "glpk"


def _termination_of(results: Any) -> str:
    """Termination condition across Pyomo's legacy and APPSI result objects."""
    legacy = getattr(getattr(results, "solver", None), "termination_condition", None)
    if legacy is not None:
        return str(legacy)
    return str(getattr(results, "termination_condition", "unknown"))


def _relative_gap(results: Any) -> float | None:
    """How far the returned plan could be from the best possible one.

    Without it a time-limited run is unreadable: a plan half a percent off the
    optimum and one forty percent off both just report "maxTimeLimit".
    """
    incumbent = getattr(results, "best_feasible_objective", None)
    bound = getattr(results, "best_objective_bound", None)
    if incumbent is None or bound is None:
        # appsi_highs hands back a legacy SolverResults despite the APPSI API,
        # so the bounds are in the Problem section rather than on the object.
        try:
            problem = results["Problem"][0]
            incumbent = float(problem["Lower bound"])
            bound = float(problem["Upper bound"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    try:
        incumbent, bound = float(incumbent), float(bound)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(incumbent) and math.isfinite(bound)) or abs(incumbent) < 1e-9:
        return None
    return float(abs(bound - incumbent) / abs(incumbent))


def _solve_model(
    model: Any,
    solver_name: str,
    settings: Settings,
    start_values: dict[int, tuple[Any, float]] | None = None,
) -> tuple[str, bool, float | None]:
    """Solve with either an APPSI backend (HiGHS) or a legacy shell solver (glpk).

    Returns (termination condition, whether a solution was loaded, relative gap).
    """
    time_limit = settings.optimizer.solver_time_limit
    gap = settings.optimizer.solver_mip_gap
    if solver_name.startswith("appsi_"):
        solver = SolverFactory(solver_name)
        solver.config.time_limit = float(time_limit)
        solver.config.mip_gap = float(gap)
        # Load manually so a time-limited incumbent isn't discarded.
        solver.config.load_solution = False
        # config alone doesn't bind for HiGHS — set its native options too.
        native = getattr(solver, "highs_options", None)
        if native is not None:
            native["time_limit"] = float(time_limit)
            native["mip_rel_gap"] = float(gap)
        if start_values:
            seeded = apply_mip_start(solver, model, start_values)
            log.info("optimize.warmstart", applied=seeded, values=len(start_values))
        results = solver.solve(model)
        condition = _termination_of(results)
        achieved_gap = _relative_gap(results)
        try:
            solver.load_vars()
        except Exception:
            return condition, False, achieved_gap
        return condition, True, achieved_gap

    solver = SolverFactory(solver_name)
    try:
        solver.options["tmlim"] = int(time_limit)
        solver.options["mipgap"] = float(gap)
    except Exception:
        pass
    results = solver.solve(model, tee=False)
    return _termination_of(results), True, None


def _plan_complete(plan: dict[int, dict[str, PlanCell]], prepared: PreparedData) -> bool:
    """Every required slot (shared meals + each profile's non-snack meals) filled."""
    snack_types = set(prepared.snack_meal_types) | {"snack"}
    for d in prepared.days:
        day = plan.get(d, {})
        for meal in prepared.shared_meal_types:
            if day.get(meal, {}).get(SHARED_KEY) is None:
                return False
        for p in prepared.profiles:
            for meal in prepared.per_user_meal_types:
                if meal in snack_types:
                    continue
                if day.get(meal, {}).get(p.name) is None:
                    return False
    return True


def _extract_whey(model: Any, prepared: PreparedData) -> dict[tuple[str, int], float]:
    out: dict[tuple[str, int], float] = {}
    if not hasattr(model, "whey"):
        return out
    for p in prepared.profiles:
        for d in prepared.days:
            value = cast(Any, model.whey[p.name, d]).value
            # Fractional scoops are fine — only include what's needed.
            if value is not None and float(value) > 0.05:
                out[(p.name, d)] = round(float(value), 2)
    return out


def _extract_portions(
    model: Any,
    prepared: PreparedData,
    plan: dict[int, dict[str, PlanCell]],
) -> dict[tuple[str, int, str], float]:
    """Per-person servings of each shared dish.

    Profiles on a fixed portion have no variable — their share is the configured
    multiplier, reported here so macros, the plan view and the shopping list all
    read servings from one place. A ready-meal slot is one full pot each, so it
    stays at the default 1.0 rather than the household split.
    """

    def _ready_slot(d: int, meal: str) -> bool:
        recipe = plan.get(d, {}).get(meal, {}).get(SHARED_KEY)
        return recipe is not None and recipe in prepared.ready_meal_ids

    out: dict[tuple[str, int, str], float] = {}
    for p in prepared.profiles:
        if not p.portion_is_flexible:
            if p.shared_portion_min != 1.0:
                for d in prepared.days:
                    for meal in prepared.shared_meal_types:
                        if not _ready_slot(d, meal):
                            out[(p.name, d, meal)] = p.shared_portion_min
            continue
        if not hasattr(model, "share"):
            continue
        for d in prepared.days:
            for meal in prepared.shared_meal_types:
                total = 0.0
                for r in prepared.recipes:
                    if not prepared.allowed_meal[(r, meal)]:
                        continue
                    value = cast(Any, model.share[p.name, r, d, meal]).value
                    if value is not None:
                        total += float(value)
                if total > 0:
                    out[(p.name, d, meal)] = round(total, 3)
    return out


def _extract_plan(model: Any, prepared: PreparedData) -> dict[int, dict[str, PlanCell]]:
    plan: dict[int, dict[str, PlanCell]] = {}
    for d in prepared.days:
        plan[d] = {}
        for m in prepared.meal_types:
            cell: PlanCell = {}
            plan[d][m] = cell
        for m in prepared.shared_meal_types:
            for r in prepared.recipes:
                value = cast(Any, model.x_shared[r, d, m]).value
                if value is not None and value > 0.5:
                    plan[d][m][SHARED_KEY] = r
                    break
            else:
                plan[d][m][SHARED_KEY] = None
        for p in prepared.profiles:
            for m in prepared.per_user_meal_types:
                for r in prepared.recipes:
                    value = cast(Any, model.x_user[p.name, r, d, m]).value
                    if value is not None and value > 0.5:
                        plan[d][m][p.name] = r
                        break
                else:
                    plan[d][m][p.name] = None
    return plan
