from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import exp
from typing import cast

import pandas as pd
from sqlalchemy import Engine

from meal_planner.config import Settings


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
        f"SELECT recipe_id, title, rating FROM meal_planning.recipe{extra_filter}",
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
        "SELECT recipe_id, per_serving_kcal, per_serving_fiber_g FROM meal_planning.recipe_nutrition",
        engine,
    )
    history = pd.read_sql(
        "SELECT recipe_id, max(planned_for) AS last_planned FROM meal_planning.meal_history GROUP BY recipe_id",
        engine,
    )
    return ModelInputs(recipes, meal_types, ingredients, nutrition, history)


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
    ingredients_canonical: list[str]
    food_groups: list[str]
    rating: dict[int, float]
    kcal: dict[int, float]
    fiber: dict[int, float]
    recency: dict[int, float]
    allowed_meal: dict[tuple[int, str], int]
    portion_met: dict[tuple[int, str], int]
    food_group_of: dict[str, str]
    portions: dict[tuple[int, str], float]
    group_portions: dict[tuple[int, str], float]


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
    else:
        kcal = dict.fromkeys(recipes_list, 0.0)
        fiber = dict.fromkeys(recipes_list, 0.0)

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

    return PreparedData(
        recipes=recipes_list,
        days=days,
        meal_types=meal_types,
        ingredients_canonical=ingredients_canonical,
        food_groups=food_groups,
        rating=rating,
        kcal=kcal,
        fiber=fiber,
        recency=recency,
        allowed_meal=allowed_meal,
        portion_met=portion_met,
        food_group_of=food_group_of,
        portions=portions,
        group_portions=group_portions,
    )
