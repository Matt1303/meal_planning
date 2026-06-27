from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from math import exp
from typing import cast

import pandas as pd
from sqlalchemy import Connection, Engine, text

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

    @classmethod
    def from_targets(cls, profile: ProfileTargets) -> ProfileSpec:
        return cls(
            name=profile.name,
            display_name=profile.display_name or profile.name,
            calories_daily_min=profile.calories_daily_min,
            calories_daily_max=profile.calories_daily_max,
            fiber_daily_min=profile.fiber_daily_min,
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
        f"SELECT recipe_id, title, rating, categories FROM meal_planning.recipe{extra_filter}",
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
NutritionDelta = dict[tuple[int, str], tuple[float, float, float, float, float]]
_MACRO_COLS = (
    "kcal_per_100g",
    "fiber_g_per_100g",
    "protein_g_per_100g",
    "fat_g_per_100g",
    "carbs_g_per_100g",
)


def _density_map(conn: Engine | Connection, canonicals: set[str]) -> dict[str, list[float]]:
    if not canonicals:
        return {}
    rows = pd.read_sql(
        text(
            f"""
            SELECT lower(ingredient_canonical) AS canon, {", ".join(_MACRO_COLS)}
            FROM meal_planning.ingredient_nutrition_cache
            WHERE lower(ingredient_canonical) = ANY(:cans)
            """
        ),
        conn,
        params={"cans": list(canonicals)},
    )
    return {
        row.canon: [float(getattr(row, col) or 0.0) for col in _MACRO_COLS]
        for row in rows.itertuples()
    }


def _portion_delta(
    base_grams: float,
    person_grams: float,
    person_density: list[float],
    base_density: list[float],
) -> tuple[float, float, float, float, float]:
    """Macro change from swapping base_grams (valued at base_density) for
    person_grams (valued at person_density), both per 100 g."""
    return cast(
        tuple[float, float, float, float, float],
        tuple(
            (person_grams * person_density[i] - base_grams * base_density[i]) / 100.0
            for i in range(5)
        ),
    )


def per_person_nutrition_deltas(conn: Engine | Connection, settings: Settings) -> NutritionDelta:
    """Per-recipe, per-person macro adjustments from per_person_portions.

    For each override line, replace the shared per-serving grams (and, when
    value_as is set, the dry-vs-cooked valuation that built the recipe nutrition)
    with the profile's serving — returning the delta to add to per-person totals.
    The baseline term mirrors nutrition aggregation exactly (grams/100 * density),
    so swapping it is loss-less.
    """
    specs = settings.optimizer.per_person_portions
    if not specs:
        return {}
    needed = {c.strip().lower() for s in specs for c in s.canonicals}
    needed |= {s.value_as.strip().lower() for s in specs if s.value_as}
    density = _density_map(conn, needed)
    declared_ids = {
        int(r)
        for (r,) in pd.read_sql(
            text("SELECT recipe_id FROM meal_planning.recipe WHERE declared_kcal IS NOT NULL"),
            conn,
        ).itertuples(index=False)
    }

    acc: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0] * 5)
    for spec in specs:
        cans = [c.strip().lower() for c in spec.canonicals]
        value_density = density.get(spec.value_as.strip().lower()) if spec.value_as else None
        estimated_clause = " AND ri.portion_estimated" if spec.estimated_only else ""
        rows = pd.read_sql(
            text(
                f"""
                SELECT ri.recipe_id, lower(ri.ingredient_canonical) AS canon,
                       ri.per_serving_grams
                FROM meal_planning.recipe_ingredient ri
                WHERE lower(ri.ingredient_canonical) = ANY(:cans)
                  AND ri.per_serving_grams IS NOT NULL
                  AND ri.sub_recipe_id IS NULL{estimated_clause}
                """
            ),
            conn,
            params={"cans": cans},
        )
        for row in rows.itertuples():
            canonical_density = density.get(row.canon)
            if canonical_density is None or pd.isna(row.per_serving_grams):
                continue
            base_grams = float(row.per_serving_grams)
            person_density = value_density or canonical_density
            # Computed recipes: the dry-rice term (base_grams * canonical_density)
            # is provably part of per_serving_kcal, so swap it for the cooked
            # per-person amount. Declared recipes carry an external total we can't
            # decompose, so only apply the marginal change at the cooked density.
            recipe_id = int(row.recipe_id)
            base_density = (
                person_density
                if (recipe_id in declared_ids and value_density)
                else canonical_density
            )
            for profile_name, grams in spec.grams.items():
                target = acc[(recipe_id, profile_name)]
                delta = _portion_delta(base_grams, grams, person_density, base_density)
                for i in range(5):
                    target[i] += delta[i]
    return {key: cast(tuple[float, float, float, float, float], tuple(v)) for key, v in acc.items()}


def filter_recipes(inputs: ModelInputs, *, min_rating: float, settings: Settings) -> ModelInputs:
    recipes = inputs.recipes.copy()
    recipes = recipes[recipes["rating"].fillna(0) >= min_rating]
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
    # (recipe_id, profile_name) -> per-person macro adjustment (default 0).
    kcal_delta: dict[tuple[int, str], float]
    fiber_delta: dict[tuple[int, str], float]
    protein_delta: dict[tuple[int, str], float]
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


def prepare(
    inputs: ModelInputs,
    settings: Settings,
    nutrition_deltas: NutritionDelta | None = None,
) -> PreparedData:
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
    daily_df = ing_df[ing_df["portion_met"].astype(bool)]
    ingredients_canonical = sorted(daily_df["ingredient_canonical"].dropna().unique().tolist())
    food_groups = list(targets.keys())

    portion_met = {(r, i): 0 for r in recipes_list for i in ingredients_canonical}
    portions = {(r, i): 0.0 for r in recipes_list for i in ingredients_canonical}
    food_group_of: dict[str, str] = {}

    for _, row in daily_df.iterrows():
        r = int(cast(int, row["recipe_id"]))
        i = str(row["ingredient_canonical"])
        portion_met[(r, i)] = 1
        food_group_of[i] = str(row["food_group"])
        portion_value = row["portions"]
        portions[(r, i)] = (
            float(portion_value)
            if portion_value is not None and not pd.isna(portion_value)
            else 0.0
        )

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

    deltas = nutrition_deltas or {}
    recipes_set = set(recipes_list)
    profile_names_set = {p.name for p in profiles}
    in_scope = [
        (key, v)
        for key, v in deltas.items()
        if key[0] in recipes_set and key[1] in profile_names_set
    ]
    kcal_delta = {key: v[0] for key, v in in_scope}
    fiber_delta = {key: v[1] for key, v in in_scope}
    protein_delta = {key: v[2] for key, v in in_scope}

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
        kcal_delta=kcal_delta,
        fiber_delta=fiber_delta,
        protein_delta=protein_delta,
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
    )
