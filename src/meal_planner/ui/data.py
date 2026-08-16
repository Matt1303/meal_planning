from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, text

from meal_planner.config import (
    FixedExtra,
    HouseholdSettings,
    OptimizerSettings,
    ProfileTargets,
    Settings,
    TopUpFruit,
    TopUpSettings,
    WheyProduct,
)
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
    # Daily Dozen credit this meal contributes: category -> {food: fraction of a
    # portion, capped at 1}. Fractions so a half-portion of spinach still counts.
    dozen: dict[str, dict[str, float]] = field(default_factory=dict)
    is_topup: bool = False
    detail: str | None = None
    # A repeat of a shared dish already cooked earlier in the week (leftovers).
    is_leftover: bool = False
    # Servings of the dish this person eats. A shared dish is split between the
    # household, so this runs above 1.0 for the bigger eater and below for the
    # smaller one.
    servings: float = 1.0


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
    # group -> (achieved capped at target, target, achieved uncapped)
    daily_dozen: dict[str, tuple[float, int, float]]
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
    portion_estimated: bool = False
    food_group: str | None = None
    # True when this line on its own meets its Daily Dozen min portion.
    dozen_qualifies: bool = False
    # Fraction of this food group's portion the line delivers, capped at 1.
    dozen_fraction: float = 0.0


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


# A shortfall smaller than this isn't worth a snack, and is usually just
# floating-point residue from the solver meeting a target near-exactly (a plan
# landing on 179.98 g of a 180 g goal was reporting a shortfall of 0.02 g, which
# the one-decimal display then rendered as the self-contradictory "~0 g").
MIN_REPORTABLE_KCAL_GAP = 100.0
MIN_REPORTABLE_GRAM_GAP = 10.0


def _shortfall(actual: float, target: float | None, minimum: float) -> float:
    """How far actual falls below target, ignoring differences too small to act on."""
    if target is None:
        return 0.0
    gap = float(target) - actual
    return gap if gap >= minimum else 0.0


def compute_gaps(
    day_kcal: float, day_fiber_g: float, day_protein_g: float, targets: ProfileTargets
) -> NutritionGaps:
    return NutritionGaps(
        kcal=_shortfall(day_kcal, targets.calories_daily_min, MIN_REPORTABLE_KCAL_GAP),
        fiber_g=_shortfall(day_fiber_g, targets.fiber_daily_min, MIN_REPORTABLE_GRAM_GAP),
        protein_g=_shortfall(day_protein_g, targets.protein_daily_min, MIN_REPORTABLE_GRAM_GAP),
    )


def _f(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _scale_meal(meal: MealEntry, servings: float) -> MealEntry:
    if abs(servings - 1.0) < 0.005:
        return meal
    return dataclasses.replace(
        meal,
        kcal=meal.kcal * servings,
        fiber_g=meal.fiber_g * servings,
        protein_g=meal.protein_g * servings,
        fat_g=meal.fat_g * servings,
        carbs_g=meal.carbs_g * servings,
        servings=servings,
    )


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
                       ri.sub_recipe_id, sub.title, ri.portion_estimated,
                       ri.food_group, ri.portion_met, ri.portions
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
        portion_estimated = bool(row[14])
        food_group = str(row[15]) if row[15] is not None else None
        dozen_qualifies = bool(row[16])
        dozen_fraction = min(_f(row[17]), 1.0)
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
                portion_estimated=portion_estimated,
                food_group=food_group,
                dozen_qualifies=dozen_qualifies,
                dozen_fraction=dozen_fraction,
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
                portion_estimated=portion_estimated,
                food_group=food_group,
                dozen_qualifies=dozen_qualifies,
                dozen_fraction=dozen_fraction,
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


def _meal_dozen(lines: list[IngredientLine], dozen_groups: set[str]) -> dict[str, dict[str, float]]:
    """Daily Dozen credit a meal contributes: category -> {food: fraction}.

    Each *distinct* food counts for its share of a portion, capped at one — so
    400 g of rice is one whole grain rather than five, but 50 g of spinach is
    0.63 of a greens serving rather than nothing.
    """
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for ln in lines:
        group = ln.food_group
        canonical = ln.ingredient_canonical
        if group in dozen_groups and canonical and ln.dozen_fraction > 0:
            running = out[group].get(canonical, 0.0) + ln.dozen_fraction
            out[group][canonical] = min(running, 1.0)
    return dict(out)


def _extra_meal(extra: FixedExtra) -> MealEntry:
    """A habitual weekday item shown as its own entry so the day's meals add up
    to the day's totals — the solver planned the rest of the day around it."""
    return MealEntry(
        meal_type="topup",
        title=extra.name,
        recipe_id=None,
        kcal=extra.kcal,
        fiber_g=extra.fiber_g,
        protein_g=extra.protein_g,
        fat_g=extra.fat_g,
        carbs_g=extra.carbs_g,
        is_topup=True,
        detail=extra.note or "a regular on this day — planned around, not chosen",
    )


def _whey_meal(whey: WheyProduct, scoops: float) -> MealEntry:
    shown = f"{scoops:.0f}" if abs(scoops - round(scoops)) < 0.05 else f"{scoops:.1f}"
    grams = scoops * whey.scoop_grams
    return MealEntry(
        meal_type="topup",
        title=f"{whey.label} — {shown} scoop{'' if shown == '1' else 's'}",
        recipe_id=None,
        kcal=scoops * whey.kcal,
        fiber_g=scoops * whey.fiber_g,
        protein_g=scoops * whey.protein_g,
        fat_g=scoops * whey.fat_g,
        carbs_g=scoops * whey.carbs_g,
        is_topup=True,
        detail=(
            f"{shown} × {whey.scoop_grams:.0f} g scoop ({grams:.0f} g) with water "
            f"(+{scoops * whey.protein_g:.0f} g protein) — allocated by the optimiser"
        ),
    )


def _fruit_topups(
    topup: TopUpSettings,
    day_dozen: dict[str, set[str]],
    dozen_targets: dict[str, int],
    day_kcal: float,
    calorie_max: float | None,
) -> list[MealEntry]:
    """Distinct fruit to fill short fruit Daily Dozen categories, but only while
    it keeps the day within the calorie ceiling (calories take priority)."""
    entries: list[MealEntry] = []
    if not topup.enabled:
        return entries
    running_kcal = day_kcal
    fruits_by_group: dict[str, list[TopUpFruit]] = defaultdict(list)
    for fruit in topup.fruits:
        fruits_by_group[fruit.food_group].append(fruit)
    for group, target in dozen_targets.items():
        options = fruits_by_group.get(group)
        if not options:
            continue
        present = {c.strip().lower() for c in day_dozen.get(group, set())}
        gap = target - len(present)
        for fruit in options:
            if gap <= 0:
                break
            if fruit.name.strip().lower() in present:
                continue
            if calorie_max is not None and running_kcal + fruit.kcal > calorie_max:
                continue  # would breach the calorie ceiling — skip
            present.add(fruit.name.strip().lower())
            running_kcal += fruit.kcal
            gap -= 1
            entries.append(
                MealEntry(
                    meal_type="topup",
                    title=f"{fruit.emoji} {fruit.name}",
                    recipe_id=None,
                    kcal=fruit.kcal,
                    fiber_g=fruit.fiber_g,
                    protein_g=fruit.protein_g,
                    fat_g=fruit.fat_g,
                    carbs_g=fruit.carbs_g,
                    is_topup=True,
                    detail=f"{fruit.grams:.0f} g — adds a {group} food",
                    dozen={group: {fruit.name: 1.0}},
                )
            )
    return entries


def load_plan_view(
    plan_run_id: int,
    opt: OptimizerSettings,
    household: HouseholdSettings,
    *,
    daily_dozen_targets: dict[str, int] | None = None,
    settings: Settings | None = None,
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

        portion_rows = conn.execute(
            text(
                """
                SELECT day, profile_id, meal_type, servings
                FROM meal_planning.plan_meal_portion
                WHERE plan_run_id = :pr
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

        day_profile_rows = conn.execute(
            text(
                """
                SELECT pdp.day, pdp.profile_id, up.name, COALESCE(up.display_name, up.name),
                       pdp.kcal, pdp.fiber_g, pdp.protein_g, pdp.fat_g, pdp.carbs_g,
                       up.calories_daily_min, up.calories_daily_max,
                       up.fiber_daily_min, up.protein_daily_min, up.protein_daily_max,
                       pdp.whey_scoops
                FROM meal_planning.plan_day_profile pdp
                JOIN meal_planning.user_profile up ON up.profile_id = pdp.profile_id
                WHERE pdp.plan_run_id = :pr
                ORDER BY pdp.day, pdp.profile_id
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

    servings_by_slot: dict[tuple[int, int, str], float] = {
        (int(r[0]), int(r[1]), str(r[2])): float(r[3]) for r in portion_rows
    }

    profile_lookup = _load_profile_records(eng, plan_run_id)

    recipe_ids = sorted({int(row[3]) for row in meal_rows if row[3] is not None})
    rating_by_recipe = _load_recipe_ratings(eng, recipe_ids)
    ingredients_by_recipe = _load_ingredient_breakdown(eng, recipe_ids)

    topup_cfg = settings.topup if settings is not None else TopUpSettings(enabled=False)
    dozen_groups = set(targets_map)
    run_date: date | None = None
    if run_row[1] is not None:
        candidate = run_row[1]
        run_date = candidate.date() if hasattr(candidate, "date") else candidate
    last_eaten_by_recipe = _load_last_eaten(eng, recipe_ids, run_date)

    # Bucket meals: per (day, profile_id) -> list of MealEntry. meal_rows is
    # ordered by day, so the first time a shared dish appears is "fresh" and any
    # later appearance for the same meal type is leftovers.
    meals_by_day_profile: dict[tuple[int, int], list[MealEntry]] = {}
    shared_meals_by_day: dict[int, list[MealEntry]] = {}
    seen_shared: set[tuple[str, int]] = set()
    for row in meal_rows:
        day_int = int(row[0])
        meal_type = str(row[1])
        profile_id = int(row[2])
        recipe_id = int(row[3]) if row[3] is not None else None
        is_leftover = False
        if profile_id == 0 and recipe_id is not None:
            if (meal_type, recipe_id) in seen_shared:
                is_leftover = True
            else:
                seen_shared.add((meal_type, recipe_id))
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
            is_leftover=is_leftover,
        )
        if profile_id == 0:
            shared_meals_by_day.setdefault(day_int, []).append(entry)
        else:
            meals_by_day_profile.setdefault((day_int, profile_id), []).append(entry)

    targets_by_name: dict[str, ProfileTargets] = {p.name: p for p in household.profiles}

    profile_id_to_name: dict[int, str] = {}
    profile_id_to_display: dict[int, str] = {}
    # Targets the plan was actually built with (from user_profile), so a loaded
    # plan's shortfall + top-ups match its own targets, not the current config.
    stored_targets: dict[int, ProfileTargets] = {}
    whey_by_day_profile: dict[tuple[int, int], float] = {}
    for row in day_profile_rows:
        pid = int(row[1])
        profile_id_to_name[pid] = str(row[2])
        profile_id_to_display[pid] = str(row[3])
        whey_by_day_profile[(int(row[0]), pid)] = float(row[14] or 0)
        if pid not in stored_targets and any(v is not None for v in row[9:14]):
            stored_targets[pid] = ProfileTargets(
                name=str(row[2]),
                display_name=str(row[3]),
                calories_daily_min=int(row[9]) if row[9] is not None else None,
                calories_daily_max=int(row[10]) if row[10] is not None else None,
                fiber_daily_min=int(row[11]) if row[11] is not None else None,
                protein_daily_min=int(row[12]) if row[12] is not None else None,
                protein_daily_max=int(row[13]) if row[13] is not None else None,
            )

    if not profile_id_to_name and profile_lookup:
        for pid, display in profile_lookup.items():
            profile_id_to_name[pid] = display
            profile_id_to_display[pid] = display

    if not profile_id_to_name:
        profile_id_to_name = {0: "default"}
        profile_id_to_display = {0: "Default"}

    days_set = sorted({row[0] for row in meal_rows} | {row[0] for row in day_profile_rows})
    days_int = [int(d) for d in days_set]

    def _enrich(meal: MealEntry, profile_name: str) -> MealEntry:
        dozen = (
            _meal_dozen(ingredients_by_recipe.get(meal.recipe_id, []), dozen_groups)
            if meal.recipe_id is not None
            else {}
        )
        return dataclasses.replace(meal, dozen=dozen)

    plan_days: list[DayPlan] = []
    for day_int in days_int:
        per_profile: list[DayPlanForProfile] = []
        shared = shared_meals_by_day.get(day_int, [])
        for profile_id, name in profile_id_to_name.items():
            display = profile_id_to_display.get(profile_id, name)
            user_meals = list(meals_by_day_profile.get((day_int, profile_id), []))
            shared_for_profile = [
                _scale_meal(m, servings_by_slot.get((day_int, profile_id, m.meal_type), 1.0))
                for m in shared
            ]
            combined = [_enrich(m, name) for m in (shared_for_profile + user_meals)]

            targets_for_profile = targets_by_name.get(name)
            if targets_for_profile is not None:
                horizon = opt.planning_horizon_days
                combined += [
                    _extra_meal(extra)
                    for extra in targets_for_profile.fixed_extras
                    if day_int in extra.days_within(horizon)
                ]

            # Whey the optimiser allocated for this person/day (protein within
            # the calorie band) — show it as a meal so totals reconcile.
            scoops = whey_by_day_profile.get((day_int, profile_id), 0.0)
            if scoops > 0.05 and topup_cfg.enabled:
                whey = settings.whey_for(name) if settings is not None else topup_cfg.default_whey
                combined.append(_whey_meal(whey, scoops))

            day_dozen: dict[str, dict[str, float]] = defaultdict(dict)
            for m in combined:
                for group, foods in m.dozen.items():
                    for food, fraction in foods.items():
                        running = day_dozen[group].get(food, 0.0) + fraction
                        day_dozen[group][food] = min(running, 1.0)

            targets = stored_targets.get(profile_id) or _targets_for(opt, targets_by_name.get(name))
            # Fruit top-ups only while they keep the day within the calorie ceiling.
            current_kcal = sum(m.kcal for m in combined)
            calorie_max = (
                float(targets.calories_daily_max)
                if targets.calories_daily_max is not None
                else None
            )
            topups = _fruit_topups(
                topup_cfg,
                {g: set(foods) for g, foods in day_dozen.items()},
                targets_map,
                current_kcal,
                calorie_max,
            )
            combined += topups
            for m in topups:
                for group, foods in m.dozen.items():
                    for food, fraction in foods.items():
                        running = day_dozen[group].get(food, 0.0) + fraction
                        day_dozen[group][food] = min(running, 1.0)

            day_kcal = sum(m.kcal for m in combined)
            day_fiber = sum(m.fiber_g for m in combined)
            day_protein = sum(m.protein_g for m in combined)
            day_fat = sum(m.fat_g for m in combined)
            day_carbs = sum(m.carbs_g for m in combined)
            daily_dozen = {}
            for group in targets_map:
                target = int(targets_map.get(group, 0))
                achieved = sum(day_dozen.get(group, {}).values())
                # Capped so a surplus in one group can't paper over a shortfall
                # in another when the day's total is summed.
                daily_dozen[group] = (min(achieved, float(target)), target, achieved)
            gaps = compute_gaps(day_kcal, day_fiber, day_protein, targets)
            per_profile.append(
                DayPlanForProfile(
                    profile_name=name,
                    display_name=display,
                    meals=combined,
                    day_kcal=day_kcal,
                    day_fiber_g=day_fiber,
                    day_protein_g=day_protein,
                    day_fat_g=day_fat,
                    day_carbs_g=day_carbs,
                    daily_dozen=daily_dozen,
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
    settings: Settings | None = None,
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
        int(row[0]),
        opt,
        household,
        daily_dozen_targets=daily_dozen_targets,
        settings=settings,
        engine=eng,
    )
