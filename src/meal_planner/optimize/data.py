from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from math import exp
from typing import cast

import pandas as pd
from sqlalchemy import Engine

from meal_planner.config import ProfileTargets, Settings
from meal_planner.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    display_name: str
    calories_daily_min: int | None
    calories_daily_max: int | None
    fiber_daily_min: int | None
    protein_daily_min: int | None
    protein_daily_max: int | None
    fixed_meals: dict[str, str] = field(default_factory=dict)
    shared_portion_min: float = 1.0
    shared_portion_max: float = 1.0

    @property
    def portion_is_flexible(self) -> bool:
        return self.shared_portion_max > self.shared_portion_min

    @classmethod
    def from_targets(cls, profile: ProfileTargets) -> ProfileSpec:
        return cls(
            name=profile.name,
            display_name=profile.display_name or profile.name,
            calories_daily_min=profile.calories_daily_min,
            calories_daily_max=profile.calories_daily_max,
            fiber_daily_min=profile.fiber_daily_min,
            shared_portion_min=profile.shared_portion_min,
            shared_portion_max=profile.shared_portion_max,
            protein_daily_min=profile.protein_daily_min,
            protein_daily_max=profile.protein_daily_max,
            fixed_meals=dict(profile.fixed_meals),
        )


def derive_profiles(settings: Settings) -> list[ProfileSpec]:
    if settings.household.profiles:
        return [ProfileSpec.from_targets(p) for p in settings.household.profiles]
    opt = settings.optimizer
    return [
        ProfileSpec(
            name="default",
            display_name="Default",
            calories_daily_min=opt.calories_daily_min,
            calories_daily_max=opt.calories_daily_max,
            fiber_daily_min=opt.fiber_daily_min,
            protein_daily_min=opt.protein_daily_min,
            protein_daily_max=opt.protein_daily_max,
        )
    ]


def split_meal_types(settings: Settings) -> tuple[list[str], list[str]]:
    shared = [m for m in settings.meal_types if m in settings.household.shared_meal_types]
    per_user = [m for m in settings.meal_types if m not in shared]
    return shared, per_user


def snack_slot_names(max_snacks: int) -> list[str]:
    """Slot meal-type names for the day's snacks. One snack keeps the plain
    'snack' name (backward compatible); 2+ become snack_1 .. snack_N."""
    if max_snacks <= 1:
        return ["snack"]
    return [f"snack_{i}" for i in range(1, max_snacks + 1)]


def _expand_snacks(meal_types: list[str], snack_slots: list[str]) -> list[str]:
    out: list[str] = []
    for m in meal_types:
        if m == "snack":
            out.extend(snack_slots)
        else:
            out.append(m)
    return out


@dataclass(frozen=True)
class ModelInputs:
    recipes: pd.DataFrame
    meal_types: pd.DataFrame
    ingredients: pd.DataFrame
    nutrition: pd.DataFrame
    history: pd.DataFrame


def load_inputs(engine: Engine, *, include_non_plant: bool) -> ModelInputs:
    extra_filter = "" if include_non_plant else " WHERE is_plant_based = TRUE"
    recipes = pd.read_sql(
        f"SELECT recipe_id, title, rating, categories, prep_minutes, cook_minutes"
        f" FROM meal_planning.recipe{extra_filter}",
        engine,
    )
    meal_types = pd.read_sql(
        "SELECT recipe_id, meal_type FROM meal_planning.recipe_meal_type",
        engine,
    )
    ingredients = pd.read_sql(
        """
        SELECT recipe_id, ingredient_canonical, food_group, portion_met, portions
        FROM meal_planning.recipe_ingredient
        WHERE ingredient_canonical IS NOT NULL AND food_group IS NOT NULL
        """,
        engine,
    )
    nutrition = pd.read_sql(
        """
        SELECT recipe_id, per_serving_kcal, per_serving_fiber_g, per_serving_protein_g
        FROM meal_planning.recipe_nutrition
        """,
        engine,
    )
    history = pd.read_sql(
        "SELECT recipe_id, max(planned_for) AS last_planned FROM meal_planning.meal_history GROUP BY recipe_id",
        engine,
    )
    return ModelInputs(recipes, meal_types, ingredients, nutrition, history)


# (recipe_id, profile_name) -> (kcal, fiber_g, protein_g, fat_g, carbs_g) adjustment.
def filter_recipes(inputs: ModelInputs, *, min_rating: float, settings: Settings) -> ModelInputs:
    recipes = inputs.recipes.copy()
    must = set(settings.optimizer.must_include_recipe_ids)
    keep = (recipes["rating"].fillna(0) >= min_rating) | recipes["recipe_id"].isin(must)
    recipes = recipes[keep]
    if recipes.empty:
        return inputs
    if not inputs.meal_types.empty:
        meal_set = set(settings.meal_types)
        recipes_with_types = inputs.meal_types[inputs.meal_types["meal_type"].isin(meal_set)][
            "recipe_id"
        ].unique()
        if len(recipes_with_types) > 0:
            recipes = recipes[recipes["recipe_id"].isin(recipes_with_types)]
    return ModelInputs(
        recipes=recipes,
        meal_types=inputs.meal_types,
        ingredients=inputs.ingredients,
        nutrition=inputs.nutrition,
        history=inputs.history,
    )


def recency_score(last_date: date | datetime | None, half_life_days: int) -> float:
    if last_date is None:
        return 0.0
    if isinstance(last_date, datetime):
        last_date = last_date.date()
    delta_days = (date.today() - last_date).days
    return exp(-delta_days / float(half_life_days))


@dataclass(frozen=True)
class PreparedData:
    recipes: list[int]
    days: list[int]
    meal_types: list[str]
    shared_meal_types: list[str]
    per_user_meal_types: list[str]
    profiles: list[ProfileSpec]
    ingredients_canonical: list[str]
    food_groups: list[str]
    rating: dict[int, float]
    kcal: dict[int, float]
    fiber: dict[int, float]
    protein: dict[int, float]
    recency: dict[int, float]
    allowed_meal: dict[tuple[int, str], int]
    portion_met: dict[tuple[int, str], int]
    food_group_of: dict[str, str]
    portions: dict[tuple[int, str], float]
    group_portions: dict[tuple[int, str], float]
    # (profile_name, meal_type) -> recipe_id pinned every day for that profile.
    fixed_assignments: dict[tuple[str, str], int] = field(default_factory=dict)
    fixed_recipe_ids: set[int] = field(default_factory=set)
    # snack slot meal-type names (e.g. ["snack_1","snack_2","snack_3"]).
    snack_meal_types: list[str] = field(default_factory=list)
    # category-substring -> recipe ids in that category (for per-day caps).
    category_recipe_ids: dict[str, set[int]] = field(default_factory=dict)
    # category-substring -> max snacks of that category per day.
    snack_category_limits: dict[str, int] = field(default_factory=dict)
    # Kitchen minutes to cook one batch of each recipe (prep + cook, scaled by
    # the user's multiplier; ready meals at their flat minutes). Absent means
    # no data — counts as zero until the recipe is timed in Paprika.
    cook_minutes: dict[int, float] = field(default_factory=dict)
    # Recipes in the ready-meal category: exempt from leftover pairing and
    # served as one full portion each rather than the household split.
    ready_meal_ids: set[int] = field(default_factory=set)


def prepare(inputs: ModelInputs, settings: Settings) -> PreparedData:
    targets = settings.daily_dozen_targets
    recipes_list = [int(r) for r in inputs.recipes["recipe_id"].tolist()]
    days = list(range(1, settings.optimizer.planning_horizon_days + 1))
    meal_types = list(settings.meal_types)

    rating: dict[int, float] = {}
    for r in recipes_list:
        sub = inputs.recipes.loc[inputs.recipes["recipe_id"] == r, "rating"].iloc[0]
        rating[r] = float(sub) if sub is not None and not pd.isna(sub) else 0.0

    if not inputs.nutrition.empty:
        nut = inputs.nutrition.set_index("recipe_id")
        kcal = {
            r: float(nut.loc[r, "per_serving_kcal"])
            if r in nut.index and not pd.isna(nut.loc[r, "per_serving_kcal"])
            else 0.0
            for r in recipes_list
        }
        fiber = {
            r: float(nut.loc[r, "per_serving_fiber_g"])
            if r in nut.index and not pd.isna(nut.loc[r, "per_serving_fiber_g"])
            else 0.0
            for r in recipes_list
        }
        if "per_serving_protein_g" in nut.columns:
            protein = {
                r: float(nut.loc[r, "per_serving_protein_g"])
                if r in nut.index and not pd.isna(nut.loc[r, "per_serving_protein_g"])
                else 0.0
                for r in recipes_list
            }
        else:
            protein = dict.fromkeys(recipes_list, 0.0)
    else:
        kcal = dict.fromkeys(recipes_list, 0.0)
        fiber = dict.fromkeys(recipes_list, 0.0)
        protein = dict.fromkeys(recipes_list, 0.0)

    history_map: dict[int, date | datetime | None] = {}
    for _, row in inputs.history.iterrows():
        rid = int(cast(int, row["recipe_id"]))
        history_map[rid] = cast("date | datetime | None", row["last_planned"])
    recency = {
        r: recency_score(history_map.get(r), settings.optimizer.recency_half_life_days)
        for r in recipes_list
    }

    if inputs.meal_types.empty:
        allowed_meal: dict[tuple[int, str], int] = {
            (r, m): 1 for r in recipes_list for m in meal_types
        }
    else:
        grouped = inputs.meal_types.groupby("recipe_id")["meal_type"].apply(list).to_dict()
        allowed_meal = {}
        for r in recipes_list:
            allowed = grouped.get(r, [])
            for m in meal_types:
                allowed_meal[(r, m)] = 1 if m in allowed else 0

    ing_df = inputs.ingredients[inputs.ingredients["recipe_id"].isin(recipes_list)].copy()
    food_groups = list(targets.keys())
    # Daily Dozen counts each distinct food as its fraction of a portion, capped
    # at one. A food that never reaches a full portion still counts for what it
    # is, so the index set is every Daily Dozen food rather than only those
    # clearing the bar — greens and flaxseed never clear it and were invisible.
    daily_df = ing_df[ing_df["food_group"].isin(food_groups)]
    ingredients_canonical = sorted(daily_df["ingredient_canonical"].dropna().unique().tolist())

    portion_met = {(r, i): 0 for r in recipes_list for i in ingredients_canonical}
    portions = {(r, i): 0.0 for r in recipes_list for i in ingredients_canonical}
    food_group_of: dict[str, str] = {}

    for _, row in daily_df.iterrows():
        r = int(cast(int, row["recipe_id"]))
        i = str(row["ingredient_canonical"])
        portion_met[(r, i)] = 1 if bool(row["portion_met"]) else 0
        food_group_of[i] = str(row["food_group"])
        portion_value = row["portions"]
        fraction = (
            float(portion_value)
            if portion_value is not None and not pd.isna(portion_value)
            else 0.0
        )
        # Capped so 240 g of one wholegrain is one grain, not three.
        portions[(r, i)] = min(fraction, 1.0)

    group_portions = {(r, g): 0.0 for r in recipes_list for g in food_groups}
    for _, row in ing_df.iterrows():
        r = int(cast(int, row["recipe_id"]))
        g = str(row["food_group"])
        if (r, g) in group_portions:
            value = row["portions"]
            group_portions[(r, g)] += (
                float(value) if value is not None and not pd.isna(value) else 0.0
            )

    profiles = derive_profiles(settings)
    shared_meal_types, per_user_meal_types = split_meal_types(settings)

    # Expand the single 'snack' meal type into up to N optional snack slots.
    snack_slots = snack_slot_names(settings.optimizer.max_snacks_per_day)
    snack_meal_types = [s for s in snack_slots if s != "snack"]  # extra slots beyond plain 'snack'
    if settings.optimizer.max_snacks_per_day > 1:
        per_user_meal_types = _expand_snacks(per_user_meal_types, snack_slots)
        shared_meal_types = _expand_snacks(shared_meal_types, snack_slots)
        meal_types = _expand_snacks(meal_types, snack_slots)
        snack_meal_types = list(snack_slots)
        # Each snack slot inherits the 'snack' allow-list.
        for r in recipes_list:
            snack_allowed = allowed_meal.get((r, "snack"), 0)
            for slot in snack_slots:
                allowed_meal[(r, slot)] = snack_allowed

    # Map each limited category (e.g. "smoothie") to the recipes in it.
    category_recipe_ids: dict[str, set[int]] = {}
    limits = settings.optimizer.snack_category_limits
    if limits and "categories" in inputs.recipes.columns:
        cat_lookup = {
            int(rid): str(cats) if cats is not None and not pd.isna(cats) else ""
            for rid, cats in zip(
                inputs.recipes["recipe_id"].tolist(),
                inputs.recipes["categories"].tolist(),
                strict=False,
            )
        }
        for category in limits:
            needle = category.strip().lower()
            category_recipe_ids[category] = {
                rid for rid, cats in cat_lookup.items() if needle in cats.lower()
            }

    # Resolve per-profile fixed meals (recipe title -> recipe_id). The pinned
    # recipe must be in the filtered recipe set and assigned to a per-user meal
    # type; we force it allowed for that meal so the pin is feasible.
    title_to_id = {
        str(t).strip().lower(): int(rid)
        for rid, t in zip(
            inputs.recipes["recipe_id"].tolist(),
            inputs.recipes["title"].tolist(),
            strict=False,
        )
    }
    fixed_assignments: dict[tuple[str, str], int] = {}
    fixed_recipe_ids: set[int] = set()
    for profile in profiles:
        for meal_type, title in profile.fixed_meals.items():
            if meal_type not in per_user_meal_types:
                log.warning(
                    "optimize.fixed_meal_not_per_user",
                    profile=profile.name,
                    meal_type=meal_type,
                )
                continue
            fixed_rid = title_to_id.get(str(title).strip().lower())
            if fixed_rid is None:
                log.warning(
                    "optimize.fixed_meal_recipe_missing",
                    profile=profile.name,
                    meal_type=meal_type,
                    title=title,
                )
                continue
            fixed_assignments[(profile.name, meal_type)] = fixed_rid
            fixed_recipe_ids.add(fixed_rid)
            allowed_meal[(fixed_rid, meal_type)] = 1

    tb = settings.optimizer.time_budget
    ready_term = settings.optimizer.ready_meal_category.strip().lower()
    cook_minutes: dict[int, float] = {}
    ready_meal_ids: set[int] = set()
    for row in inputs.recipes.itertuples():
        rid = int(row.recipe_id)
        if rid not in set(recipes_list):
            continue
        cats = str(getattr(row, "categories", "") or "").lower()
        if ready_term and ready_term in cats:
            ready_meal_ids.add(rid)
            cook_minutes[rid] = float(tb.ready_meal_minutes)
            continue
        prep = getattr(row, "prep_minutes", None)
        cook = getattr(row, "cook_minutes", None)
        total = (0 if prep is None or pd.isna(prep) else float(prep)) + (
            0 if cook is None or pd.isna(cook) else float(cook)
        )
        if total > 0:
            cook_minutes[rid] = total * tb.time_multiplier

    return PreparedData(
        recipes=recipes_list,
        days=days,
        meal_types=meal_types,
        shared_meal_types=shared_meal_types,
        per_user_meal_types=per_user_meal_types,
        profiles=profiles,
        ingredients_canonical=ingredients_canonical,
        food_groups=food_groups,
        rating=rating,
        kcal=kcal,
        fiber=fiber,
        protein=protein,
        recency=recency,
        allowed_meal=allowed_meal,
        portion_met=portion_met,
        food_group_of=food_group_of,
        portions=portions,
        group_portions=group_portions,
        fixed_assignments=fixed_assignments,
        fixed_recipe_ids=fixed_recipe_ids,
        snack_meal_types=snack_meal_types,
        category_recipe_ids=category_recipe_ids,
        snack_category_limits=dict(settings.optimizer.snack_category_limits),
        cook_minutes=cook_minutes,
        ready_meal_ids=ready_meal_ids,
    )
