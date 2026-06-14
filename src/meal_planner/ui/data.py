from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, text

from meal_planner.config import HouseholdSettings, OptimizerSettings, ProfileTargets
from meal_planner.db import get_engine


@dataclass(frozen=True)
class MealEntry:
    meal_type: str
    title: str | None
    recipe_id: int | None
    kcal: float
    fiber_g: float
    protein_g: float
    fat_g: float
    carbs_g: float
    rating: float | None = None
    last_eaten: date | None = None


@dataclass(frozen=True)
class NutritionGaps:
    kcal: float = 0.0
    fiber_g: float = 0.0
    protein_g: float = 0.0

    @property
    def any_shortfall(self) -> bool:
        return self.kcal > 0 or self.fiber_g > 0 or self.protein_g > 0


@dataclass(frozen=True)
class DayPlanForProfile:
    profile_name: str
    display_name: str
    meals: list[MealEntry]
    day_kcal: float
    day_fiber_g: float
    day_protein_g: float
    day_fat_g: float
    day_carbs_g: float
    daily_dozen: dict[str, tuple[int, int, float]]
    gaps: NutritionGaps = field(default_factory=NutritionGaps)

    def empty_snack_slots(self) -> list[MealEntry]:
        return [m for m in self.meals if m.meal_type == "snack" and m.title is None]


@dataclass(frozen=True)
class DayPlan:
    day: int
    per_profile: list[DayPlanForProfile]


@dataclass(frozen=True)
class IngredientLine:
    raw_text: str
    ingredient_canonical: str | None
    per_serving_grams: float | None
    kcal: float
    protein_g: float
    fiber_g: float
    fat_g: float
    carbs_g: float
    match_source_name: str | None
    match_score: float | None
    source: str | None
    sub_recipe_id: int | None = None
    sub_recipe_title: str | None = None


@dataclass(frozen=True)
class PlanView:
    plan_run_id: int
    run_time: str
    solver_status: str
    relaxation_level: int
    slack_total: float
    correlation_id: str | None
    days: list[DayPlan]
    recipe_ingredients: dict[int, list[IngredientLine]] = field(default_factory=dict)


def _targets_for(opt: OptimizerSettings, profile: ProfileTargets | None) -> ProfileTargets:
    if profile is not None:
        return profile
    return ProfileTargets(
        name="default",
        display_name="Default",
        calories_daily_min=opt.calories_daily_min,
        calories_daily_max=opt.calories_daily_max,
        fiber_daily_min=opt.fiber_daily_min,
        protein_daily_min=opt.protein_daily_min,
        protein_daily_max=opt.protein_daily_max,
    )


def compute_gaps(
    day_kcal: float, day_fiber_g: float, day_protein_g: float, targets: ProfileTargets
) -> NutritionGaps:
    kcal_gap = 0.0
    fiber_gap = 0.0
    protein_gap = 0.0
    if targets.calories_daily_min is not None and day_kcal < float(targets.calories_daily_min):
        kcal_gap = float(targets.calories_daily_min) - day_kcal
    if targets.fiber_daily_min is not None and day_fiber_g < float(targets.fiber_daily_min):
        fiber_gap = float(targets.fiber_daily_min) - day_fiber_g
    if targets.protein_daily_min is not None and day_protein_g < float(targets.protein_daily_min):
        protein_gap = float(targets.protein_daily_min) - day_protein_g
    return NutritionGaps(kcal=kcal_gap, fiber_g=fiber_gap, protein_g=protein_gap)


def _f(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _load_profile_records(engine: Engine, plan_run_id: int) -> dict[int, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT up.profile_id, COALESCE(up.display_name, up.name) AS display
                FROM meal_planning.plan_meal pm
                JOIN meal_planning.user_profile up ON up.profile_id = pm.profile_id
                WHERE pm.plan_run_id = :pr
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def _load_recipe_ratings(engine: Engine, recipe_ids: list[int]) -> dict[int, float]:
    if not recipe_ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT recipe_id, rating FROM meal_planning.recipe
                WHERE recipe_id = ANY(:ids) AND rating IS NOT NULL
                """
            ),
            {"ids": recipe_ids},
        ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def _load_recipe_per_gram_macros(
    engine: Engine,
) -> dict[int, tuple[float, float, float, float, float]]:
    """For every recipe, derive per-gram macros from its non-sub ingredients
    so a parent line that points at this recipe can be scaled. Returns
    (kcal, protein, fiber, fat, carbs) all per-gram, keyed by recipe_id."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ri.recipe_id,
                       SUM(ri.per_serving_grams) AS total_grams,
                       SUM(ri.per_serving_grams * c.kcal_per_100g) / 100.0 AS kcal,
                       SUM(ri.per_serving_grams * c.protein_g_per_100g) / 100.0 AS protein,
                       SUM(ri.per_serving_grams * c.fiber_g_per_100g) / 100.0 AS fiber,
                       SUM(ri.per_serving_grams * c.fat_g_per_100g) / 100.0 AS fat,
                       SUM(ri.per_serving_grams * c.carbs_g_per_100g) / 100.0 AS carbs
                FROM meal_planning.recipe_ingredient ri
                JOIN meal_planning.ingredient_nutrition_cache c
                  ON c.ingredient_canonical = ri.ingredient_canonical
                WHERE ri.per_serving_grams IS NOT NULL
                  AND ri.sub_recipe_id IS NULL
                GROUP BY ri.recipe_id
                """
            )
        ).fetchall()
    out: dict[int, tuple[float, float, float, float, float]] = {}
    for row in rows:
        total = _f(row[1])
        if total <= 0:
            continue
        out[int(row[0])] = (
            _f(row[2]) / total,
            _f(row[3]) / total,
            _f(row[4]) / total,
            _f(row[5]) / total,
            _f(row[6]) / total,
        )
    return out


def _load_ingredient_breakdown(
    engine: Engine, recipe_ids: list[int]
) -> dict[int, list[IngredientLine]]:
    if not recipe_ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ri.recipe_id, ri.raw_text, ri.ingredient_canonical,
                       ri.per_serving_grams,
                       c.kcal_per_100g, c.protein_g_per_100g, c.fiber_g_per_100g,
                       c.fat_g_per_100g, c.carbs_g_per_100g,
                       c.match_source_name, c.match_score, c.source,
                       ri.sub_recipe_id, sub.title
                FROM meal_planning.recipe_ingredient ri
                LEFT JOIN meal_planning.ingredient_nutrition_cache c
                       ON c.ingredient_canonical = ri.ingredient_canonical
                LEFT JOIN meal_planning.recipe sub
                       ON sub.recipe_id = ri.sub_recipe_id
                WHERE ri.recipe_id = ANY(:ids)
                ORDER BY ri.recipe_id, ri.raw_text
                """
            ),
            {"ids": recipe_ids},
        ).fetchall()

    sub_per_gram = _load_recipe_per_gram_macros(engine)
    result: dict[int, list[IngredientLine]] = {}
    for row in rows:
        recipe_id = int(row[0])
        grams = float(row[3]) if row[3] is not None else None
        sub_recipe_id = int(row[12]) if row[12] is not None else None
        sub_recipe_title = str(row[13]) if row[13] is not None else None
        if sub_recipe_id is not None and grams is not None and sub_recipe_id in sub_per_gram:
            kcal_pg, protein_pg, fiber_pg, fat_pg, carbs_pg = sub_per_gram[sub_recipe_id]
            line = IngredientLine(
                raw_text=str(row[1]),
                ingredient_canonical=None,
                per_serving_grams=grams,
                kcal=kcal_pg * grams,
                protein_g=protein_pg * grams,
                fiber_g=fiber_pg * grams,
                fat_g=fat_pg * grams,
                carbs_g=carbs_pg * grams,
                match_source_name=f"recipe: {sub_recipe_title}",
                match_score=100.0,
                source="sub_recipe",
                sub_recipe_id=sub_recipe_id,
                sub_recipe_title=sub_recipe_title,
            )
        else:
            kcal_per_100g = _f(row[4])
            protein_per_100g = _f(row[5])
            fiber_per_100g = _f(row[6])
            fat_per_100g = _f(row[7])
            carbs_per_100g = _f(row[8])
            factor = (grams / 100.0) if grams is not None else 0.0
            line = IngredientLine(
                raw_text=str(row[1]),
                ingredient_canonical=str(row[2]) if row[2] is not None else None,
                per_serving_grams=grams,
                kcal=kcal_per_100g * factor,
                protein_g=protein_per_100g * factor,
                fiber_g=fiber_per_100g * factor,
                fat_g=fat_per_100g * factor,
                carbs_g=carbs_per_100g * factor,
                match_source_name=str(row[9]) if row[9] is not None else None,
                match_score=float(row[10]) if row[10] is not None else None,
                source=str(row[11]) if row[11] is not None else None,
                sub_recipe_id=sub_recipe_id,
                sub_recipe_title=sub_recipe_title,
            )
        result.setdefault(recipe_id, []).append(line)
    return result


def _load_last_eaten(engine: Engine, recipe_ids: list[int], before: date | None) -> dict[int, date]:
    if not recipe_ids:
        return {}
    params: dict[str, object] = {"ids": recipe_ids}
    where_before = ""
    if before is not None:
        where_before = "AND planned_for < :before"
        params["before"] = before
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT recipe_id, MAX(planned_for)
                FROM meal_planning.meal_history
                WHERE recipe_id = ANY(:ids) {where_before}
                GROUP BY recipe_id
                """
            ),
            params,
        ).fetchall()
    return {int(r[0]): r[1] for r in rows if r[1] is not None}


def load_plan_view(
    plan_run_id: int,
    opt: OptimizerSettings,
    household: HouseholdSettings,
    *,
    daily_dozen_targets: dict[str, int] | None = None,
    engine: Engine | None = None,
) -> PlanView | None:
    targets_map = daily_dozen_targets or {}
    eng = engine or get_engine()
    with eng.connect() as conn:
        run_row = conn.execute(
            text(
                """
                SELECT plan_run_id, run_time, COALESCE(solver_status, ''),
                       COALESCE(relaxation_level, 0), COALESCE(slack_total, 0), correlation_id
                FROM meal_planning.plan_run WHERE plan_run_id = :pr
                """
            ),
            {"pr": plan_run_id},
        ).fetchone()
        if run_row is None:
            return None

        meal_rows = conn.execute(
            text(
                """
                SELECT pm.day, pm.meal_type, pm.profile_id, pm.recipe_id, r.title,
                       rn.per_serving_kcal, rn.per_serving_fiber_g,
                       rn.per_serving_protein_g, rn.per_serving_fat_g, rn.per_serving_carbs_g
                FROM meal_planning.plan_meal pm
                LEFT JOIN meal_planning.recipe r ON r.recipe_id = pm.recipe_id
                LEFT JOIN meal_planning.recipe_nutrition rn ON rn.recipe_id = pm.recipe_id
                WHERE pm.plan_run_id = :pr
                ORDER BY pm.day, pm.meal_type
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

        day_profile_rows = conn.execute(
            text(
                """
                SELECT pdp.day, pdp.profile_id, up.name, COALESCE(up.display_name, up.name),
                       pdp.kcal, pdp.fiber_g, pdp.protein_g, pdp.fat_g, pdp.carbs_g
                FROM meal_planning.plan_day_profile pdp
                JOIN meal_planning.user_profile up ON up.profile_id = pdp.profile_id
                WHERE pdp.plan_run_id = :pr
                ORDER BY pdp.day, pdp.profile_id
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

        group_rows = conn.execute(
            text(
                """
                SELECT day, food_group, daily_count, daily_portions
                FROM meal_planning.plan_day_group WHERE plan_run_id = :pr
                ORDER BY day, food_group
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

    profile_lookup = _load_profile_records(eng, plan_run_id)

    recipe_ids = sorted({int(row[3]) for row in meal_rows if row[3] is not None})
    rating_by_recipe = _load_recipe_ratings(eng, recipe_ids)
    ingredients_by_recipe = _load_ingredient_breakdown(eng, recipe_ids)
    run_date: date | None = None
    if run_row[1] is not None:
        candidate = run_row[1]
        run_date = candidate.date() if hasattr(candidate, "date") else candidate
    last_eaten_by_recipe = _load_last_eaten(eng, recipe_ids, run_date)

    # Bucket meals: per (day, profile_id) -> list of MealEntry
    meals_by_day_profile: dict[tuple[int, int], list[MealEntry]] = {}
    shared_meals_by_day: dict[int, list[MealEntry]] = {}
    for row in meal_rows:
        day_int = int(row[0])
        meal_type = str(row[1])
        profile_id = int(row[2])
        recipe_id = int(row[3]) if row[3] is not None else None
        entry = MealEntry(
            meal_type=meal_type,
            title=row[4],
            recipe_id=recipe_id,
            kcal=_f(row[5]),
            fiber_g=_f(row[6]),
            protein_g=_f(row[7]),
            fat_g=_f(row[8]),
            carbs_g=_f(row[9]),
            rating=rating_by_recipe.get(recipe_id) if recipe_id is not None else None,
            last_eaten=last_eaten_by_recipe.get(recipe_id) if recipe_id is not None else None,
        )
        if profile_id == 0:
            shared_meals_by_day.setdefault(day_int, []).append(entry)
        else:
            meals_by_day_profile.setdefault((day_int, profile_id), []).append(entry)

    targets_by_name: dict[str, ProfileTargets] = {p.name: p for p in household.profiles}

    groups_by_day: dict[int, dict[str, tuple[int, int, float]]] = {}
    for row in group_rows:
        day_int = int(row[0])
        groups_by_day.setdefault(day_int, {})[str(row[1])] = (
            int(row[2] or 0),
            int(targets_map.get(str(row[1]), 0)),
            _f(row[3]),
        )

    profile_id_to_name: dict[int, str] = {}
    profile_id_to_display: dict[int, str] = {}
    for row in day_profile_rows:
        profile_id_to_name[int(row[1])] = str(row[2])
        profile_id_to_display[int(row[1])] = str(row[3])

    if not profile_id_to_name and profile_lookup:
        for pid, display in profile_lookup.items():
            profile_id_to_name[pid] = display
            profile_id_to_display[pid] = display

    if not profile_id_to_name:
        profile_id_to_name = {0: "default"}
        profile_id_to_display = {0: "Default"}

    profile_totals: dict[tuple[int, int], tuple[float, float, float, float, float]] = {
        (int(row[0]), int(row[1])): (_f(row[4]), _f(row[5]), _f(row[6]), _f(row[7]), _f(row[8]))
        for row in day_profile_rows
    }

    days_set = sorted({row[0] for row in meal_rows} | {row[0] for row in day_profile_rows})
    days_int = [int(d) for d in days_set]

    plan_days: list[DayPlan] = []
    for day_int in days_int:
        per_profile: list[DayPlanForProfile] = []
        shared = shared_meals_by_day.get(day_int, [])
        for profile_id, name in profile_id_to_name.items():
            display = profile_id_to_display.get(profile_id, name)
            user_meals = list(meals_by_day_profile.get((day_int, profile_id), []))
            combined = list(shared) + user_meals
            totals = profile_totals.get(
                (day_int, profile_id),
                (
                    sum(m.kcal for m in combined),
                    sum(m.fiber_g for m in combined),
                    sum(m.protein_g for m in combined),
                    sum(m.fat_g for m in combined),
                    sum(m.carbs_g for m in combined),
                ),
            )
            targets = _targets_for(opt, targets_by_name.get(name))
            gaps = compute_gaps(totals[0], totals[1], totals[2], targets)
            per_profile.append(
                DayPlanForProfile(
                    profile_name=name,
                    display_name=display,
                    meals=combined,
                    day_kcal=totals[0],
                    day_fiber_g=totals[1],
                    day_protein_g=totals[2],
                    day_fat_g=totals[3],
                    day_carbs_g=totals[4],
                    daily_dozen=groups_by_day.get(day_int, {}),
                    gaps=gaps,
                )
            )
        plan_days.append(DayPlan(day=day_int, per_profile=per_profile))

    return PlanView(
        plan_run_id=int(run_row[0]),
        run_time=str(run_row[1]),
        solver_status=str(run_row[2]),
        relaxation_level=int(run_row[3]),
        slack_total=float(run_row[4] or 0),
        correlation_id=run_row[5],
        days=plan_days,
        recipe_ingredients=ingredients_by_recipe,
    )


def load_latest_plan_view(
    opt: OptimizerSettings,
    household: HouseholdSettings,
    *,
    daily_dozen_targets: dict[str, int] | None = None,
    engine: Engine | None = None,
) -> PlanView | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1")
        ).fetchone()
    if row is None:
        return None
    return load_plan_view(
        int(row[0]), opt, household, daily_dozen_targets=daily_dozen_targets, engine=eng
    )
