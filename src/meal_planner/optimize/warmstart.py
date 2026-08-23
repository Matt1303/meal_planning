"""Seed the solver with the latest stored plan as a MIP start.

The model never converges inside the time limit (the gap closes roughly
logarithmically), so the returned plan is whatever incumbent the search holds
when the clock stops. Handing HiGHS last week's plan as a starting incumbent
means pruning starts from a good bound at minute zero instead of minute
fifteen — and successive weeks drift less, because the search explores around
a known-good plan rather than rediscovering one.

Only integer values need deriving: HiGHS fixes the integers from the start
vector and LP-solves the continuous columns (portions, whey, slacks). Every
nutrition constraint is soft, so any assignment satisfying the hard structure
is feasible. If the stored plan no longer fits the current config — a recipe
dropped from the pool, the horizon changed — the start is abandoned and the
solve is simply cold, which is what happened before this existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text

from meal_planner.logging import get_logger
from meal_planner.optimize.data import PreparedData

log = get_logger(__name__)


@dataclass(frozen=True)
class PreviousPlan:
    # (day, meal_type) -> recipe_id for shared slots.
    shared: dict[tuple[int, str], int]
    # (profile_name, day) -> recipe ids in that person's snack slots, in order.
    snacks: dict[tuple[str, int], list[int]]
    # (profile_name, day, meal_type) -> recipe_id for non-snack per-user slots.
    per_user: dict[tuple[str, int, str], int]
    # (profile_name, day) -> whey scoops.
    whey: dict[tuple[str, int], float]


def load_previous_plan(engine: Engine) -> PreviousPlan | None:
    with engine.connect() as conn:
        run = conn.execute(
            text("SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1")
        ).fetchone()
        if run is None:
            return None
        plan_run_id = int(run[0])
        meal_rows = conn.execute(
            text(
                """
                SELECT pm.day, pm.meal_type, pm.profile_id, up.name, pm.recipe_id
                FROM meal_planning.plan_meal pm
                LEFT JOIN meal_planning.user_profile up ON up.profile_id = pm.profile_id
                WHERE pm.plan_run_id = :pr AND pm.recipe_id IS NOT NULL
                ORDER BY pm.day, pm.meal_type
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()
        whey_rows = conn.execute(
            text(
                """
                SELECT pdp.day, up.name, pdp.whey_scoops
                FROM meal_planning.plan_day_profile pdp
                JOIN meal_planning.user_profile up ON up.profile_id = pdp.profile_id
                WHERE pdp.plan_run_id = :pr
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

    shared: dict[tuple[int, str], int] = {}
    snacks: dict[tuple[str, int], list[int]] = {}
    per_user: dict[tuple[str, int, str], int] = {}
    for day, meal_type, profile_id, name, recipe_id in meal_rows:
        day_i, meal, rid = int(day), str(meal_type), int(recipe_id)
        if int(profile_id) == 0:
            shared[(day_i, meal)] = rid
        elif meal.startswith("snack"):
            snacks.setdefault((str(name), day_i), []).append(rid)
        else:
            per_user[(str(name), day_i, meal)] = rid
    whey = {(str(n), int(d)): float(w or 0) for d, n, w in whey_rows}
    return PreviousPlan(shared=shared, snacks=snacks, per_user=per_user, whey=whey)


def build_start_values(
    model: Any, prepared: PreparedData, previous: PreviousPlan, min_whey_scoops: float
) -> dict[int, tuple[Any, float]] | None:
    """Integer variable values reproducing the previous plan, keyed by id() —
    Pyomo variables overload __eq__ for expression building and are unhashable.
    None when the plan no longer fits the current pool/config (a cold start is
    the right answer then).
    """
    pool = set(prepared.recipes)
    values: dict[int, tuple[Any, float]] = {}

    def _set(var: Any, value: float) -> None:
        values[id(var)] = (var, value)

    # Shared slots: every slot must be filled, so a missing or now-ineligible
    # recipe means the start cannot satisfy the slot constraint — abandon.
    used_pairs: dict[tuple[int, str], list[int]] = {}
    for d in prepared.days:
        for meal in prepared.shared_meal_types:
            rid = previous.shared.get((d, meal))
            if rid is None or rid not in pool or not prepared.allowed_meal.get((rid, meal)):
                return None
            _set(model.x_shared[rid, d, meal], 1.0)
            used_pairs.setdefault((rid, meal), []).append(d)

    if hasattr(model, "leftover_used"):
        for (rid, meal), days in used_pairs.items():
            if rid in prepared.ready_meal_ids:
                continue
            if len(days) != 2:
                return None  # pairing violated; integer fix would be infeasible
            _set(model.leftover_used[rid, meal], 1.0)

    profile_names = {p.name for p in prepared.profiles}
    if any(name not in profile_names for name, _, _ in previous.per_user):
        return None

    # Per-user non-snack slots (breakfast is pinned anyway, but set it so the
    # start is complete).
    for (name, d, meal), rid in previous.per_user.items():
        if d not in prepared.days or meal not in prepared.per_user_meal_types:
            continue
        if rid not in pool or not prepared.allowed_meal.get((rid, meal)):
            return None
        _set(model.x_user[name, rid, d, meal], 1.0)

    # Snacks: renumbered onto the lowest slots so the fill-order constraint
    # holds even if the previous plan used higher slot numbers.
    snack_slots = [m for m in prepared.per_user_meal_types if m.startswith("snack")]
    for (name, d), rids in previous.snacks.items():
        if d not in prepared.days:
            continue
        kept = [r for r in rids if r in pool][: len(snack_slots)]
        for slot, rid in zip(snack_slots, kept, strict=False):
            if prepared.allowed_meal.get((rid, slot)):
                _set(model.x_user[name, rid, d, slot], 1.0)

    # Derived binaries, so the integer fixing is internally consistent.
    if hasattr(model, "appears_on_day"):
        appears: dict[tuple[int, int], float] = {}
        for (rid, meal), days in used_pairs.items():
            for d in days:
                appears[(rid, d)] = 1.0
        for (name, d, meal), rid in previous.per_user.items():
            if d in prepared.days and meal in prepared.per_user_meal_types and rid in pool:
                appears[(rid, d)] = 1.0
        for (name, d), rids in previous.snacks.items():
            if d in prepared.days:
                for rid in rids:
                    if rid in pool:
                        appears[(rid, d)] = 1.0
        for r in prepared.recipes:
            for d in prepared.days:
                _set(model.appears_on_day[r, d], appears.get((r, d), 0.0))
        if hasattr(model, "recipe_pair"):
            for r in prepared.recipes:
                for d1, d2 in model.PAIRS:
                    both = appears.get((r, d1), 0.0) + appears.get((r, d2), 0.0)
                    _set(model.recipe_pair[r, d1, d2], 1.0 if both > 1.5 else 0.0)

    if hasattr(model, "whey_used"):
        for p in prepared.profiles:
            for d in prepared.days:
                scoops = previous.whey.get((p.name, d), 0.0)
                _set(model.whey_used[p.name, d], 1.0 if scoops >= min_whey_scoops else 0.0)

    # y (food eaten this week): only claim it when the previous meals clearly
    # deliver a full portion across the week — a wrong 1 makes the LP
    # completion infeasible and the whole start gets discarded.
    per_person_daily: dict[str, dict[int, list[int]]] = {p.name: {} for p in prepared.profiles}
    for (rid, meal), days in used_pairs.items():
        for name in per_person_daily:
            for d in days:
                per_person_daily[name].setdefault(d, []).append(rid)
    for (name, d, meal), rid in previous.per_user.items():
        if name in per_person_daily and d in prepared.days:
            per_person_daily[name].setdefault(d, []).append(rid)
    for (name, d), rids in previous.snacks.items():
        if name in per_person_daily and d in prepared.days:
            per_person_daily[name].setdefault(d, []).extend(r for r in rids if r in pool)

    for p in prepared.profiles:
        share = p.shared_portion_min if not p.portion_is_flexible else 1.0
        for i in prepared.ingredients_canonical:
            weekly = 0.0
            for d, rids in per_person_daily[p.name].items():
                daily = sum(prepared.portions.get((r, i), 0.0) * share for r in rids)
                weekly += min(daily, 1.0)
            _set(model.y[p.name, i], 1.0 if weekly >= 1.05 else 0.0)

    return values


def apply_mip_start(solver: Any, model: Any, values: dict[int, tuple[Any, float]]) -> bool:
    """Push the start into HiGHS through the appsi wrapper's internals.

    The wrapper has no MIP-start plumbing, so this reaches for _solver_model
    (the highspy Highs) and _pyomo_var_to_solver_var_map. Guarded: if a Pyomo
    upgrade renames them, the solve degrades to a cold start and says so.
    """
    try:
        import highspy

        solver.set_instance(model)
        highs = solver._solver_model
        var_map = solver._pyomo_var_to_solver_var_map
        col_value = [0.0] * highs.getNumCol()
        for var, value in values.values():
            col_value[var_map[id(var)]] = value
        solution = highspy.HighsSolution()
        solution.col_value = col_value
        highs.setSolution(solution)
        return True
    except Exception as exc:
        log.warning("optimize.warmstart_failed", error=str(exc)[:200])
        return False
