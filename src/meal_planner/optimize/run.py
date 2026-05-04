from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, cast

from pyomo.environ import SolverFactory
from pyomo.opt import SolverResults, TerminationCondition
from sqlalchemy import Engine

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName
from meal_planner.optimize.data import PreparedData, filter_recipes, load_inputs, prepare
from meal_planner.optimize.model import ModelOptions, build_model, total_slack, variable_count

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
            enforce_weekly_kcal=True,
            enforce_weekly_fiber=True,
            enforce_group_targets=True,
            enforce_weekly_groups=True,
        ),
    ),
    (
        RelaxationLevel.DROP_DAILY_NUTRITION,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_weekly_kcal=True,
            enforce_weekly_fiber=True,
            enforce_group_targets=True,
            enforce_weekly_groups=True,
        ),
    ),
    (
        RelaxationLevel.DROP_WEEKLY_GROUPS,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_weekly_kcal=False,
            enforce_weekly_fiber=False,
            enforce_group_targets=True,
            enforce_weekly_groups=False,
        ),
    ),
    (
        RelaxationLevel.DROP_GROUP_TARGETS,
        ModelOptions(
            enforce_daily_kcal=False,
            enforce_daily_fiber=False,
            enforce_weekly_kcal=False,
            enforce_weekly_fiber=False,
            enforce_group_targets=False,
            enforce_weekly_groups=False,
        ),
    ),
]


@dataclass(frozen=True)
class OptimizeResult:
    plan: dict[int, dict[str, int | None]]
    solver_status: str
    solver_seconds: float
    slack_total: float
    relaxation_level: int
    prepared: PreparedData


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

    last_error: str = "not attempted"
    for level, options in _LEVELS:
        log.info("optimize.attempt", level=level.name)
        model = build_model(prepared, settings, options)
        solver = SolverFactory("glpk")
        if settings.optimizer.solver_time_limit:
            try:
                solver.options["tmlim"] = int(settings.optimizer.solver_time_limit)
            except Exception:
                pass
        try:
            solver.options["mipgap"] = float(settings.optimizer.solver_mip_gap)
        except Exception:
            pass
        start = time.time()
        try:
            res: SolverResults = solver.solve(model, tee=False)
        except Exception as exc:
            last_error = str(exc)
            log.warning("optimize.solver_error", level=level.name, error=last_error)
            continue
        seconds = time.time() - start
        condition = res.solver.termination_condition
        if condition == TerminationCondition.optimal:
            plan = _extract_plan(model, prepared)
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
                level=level.name,
                seconds=seconds,
                slack_total=slack,
            )
            return OptimizeResult(
                plan=plan,
                solver_status=str(condition),
                solver_seconds=seconds,
                slack_total=slack,
                relaxation_level=int(level),
                prepared=prepared,
            )
        last_error = str(condition)
        log.warning("optimize.infeasible", level=level.name, condition=last_error)

    raise RuntimeError(f"all relaxation levels failed: {last_error}")


def _extract_plan(model: Any, prepared: PreparedData) -> dict[int, dict[str, int | None]]:
    plan: dict[int, dict[str, int | None]] = {
        d: dict.fromkeys(prepared.meal_types, None) for d in prepared.days
    }
    for d in prepared.days:
        for m in prepared.meal_types:
            for r in prepared.recipes:
                value = cast(Any, model.x[r, d, m]).value
                if value is not None and value > 0.5:
                    plan[d][m] = r
                    break
    return plan
