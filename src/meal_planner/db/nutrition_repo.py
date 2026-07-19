from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class CachedNutrition:
    kcal_per_100g: Decimal | None
    fiber_g_per_100g: Decimal | None
    protein_g_per_100g: Decimal | None
    fat_g_per_100g: Decimal | None
    carbs_g_per_100g: Decimal | None
    source: str | None
    match_score: Decimal | None = None
    match_source_name: str | None = None


def fetch_cache(conn: Connection, ingredient_canonical: str) -> CachedNutrition | None:
    row = conn.execute(
        text(
            """
            SELECT kcal_per_100g, fiber_g_per_100g, protein_g_per_100g,
                   fat_g_per_100g, carbs_g_per_100g, source,
                   match_score, match_source_name
            FROM meal_planning.ingredient_nutrition_cache
            WHERE ingredient_canonical = :ing
            """
        ),
        {"ing": ingredient_canonical},
    ).fetchone()
    if row is None:
        return None
    return CachedNutrition(
        kcal_per_100g=row[0],
        fiber_g_per_100g=row[1],
        protein_g_per_100g=row[2],
        fat_g_per_100g=row[3],
        carbs_g_per_100g=row[4],
        source=row[5],
        match_score=row[6],
        match_source_name=row[7],
    )


def upsert_cache(
    conn: Connection,
    *,
    ingredient_canonical: str,
    kcal_per_100g: Decimal | None,
    fiber_g_per_100g: Decimal | None,
    protein_g_per_100g: Decimal | None,
    fat_g_per_100g: Decimal | None,
    carbs_g_per_100g: Decimal | None,
    source: str | None,
    match_score: Decimal | None,
    match_source_name: str | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.ingredient_nutrition_cache
                (ingredient_canonical, kcal_per_100g, fiber_g_per_100g,
                 protein_g_per_100g, fat_g_per_100g, carbs_g_per_100g,
                 source, match_score, match_source_name, updated_at)
            VALUES (:ing, :kcal, :fiber, :protein, :fat, :carbs,
                    :source, :score, :name, now())
            ON CONFLICT (ingredient_canonical) DO UPDATE SET
                kcal_per_100g = EXCLUDED.kcal_per_100g,
                fiber_g_per_100g = EXCLUDED.fiber_g_per_100g,
                protein_g_per_100g = EXCLUDED.protein_g_per_100g,
                fat_g_per_100g = EXCLUDED.fat_g_per_100g,
                carbs_g_per_100g = EXCLUDED.carbs_g_per_100g,
                source = EXCLUDED.source,
                match_score = EXCLUDED.match_score,
                match_source_name = EXCLUDED.match_source_name,
                updated_at = now()
            """
        ),
        {
            "ing": ingredient_canonical,
            "kcal": kcal_per_100g,
            "fiber": fiber_g_per_100g,
            "protein": protein_g_per_100g,
            "fat": fat_g_per_100g,
            "carbs": carbs_g_per_100g,
            "source": source,
            "score": match_score,
            "name": match_source_name,
        },
    )


def upsert_recipe_nutrition(
    conn: Connection,
    *,
    recipe_id: int,
    calories_kcal: Decimal,
    fiber_g: Decimal,
    per_serving_kcal: Decimal,
    per_serving_fiber_g: Decimal,
    protein_g: Decimal | None,
    fat_g: Decimal | None,
    carbs_g: Decimal | None,
    per_serving_protein_g: Decimal | None = None,
    per_serving_fat_g: Decimal | None = None,
    per_serving_carbs_g: Decimal | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.recipe_nutrition
                (recipe_id, calories_kcal, fiber_g, per_serving_kcal, per_serving_fiber_g,
                 protein_g, fat_g, carbs_g,
                 per_serving_protein_g, per_serving_fat_g, per_serving_carbs_g)
            VALUES (:rid, :kcal, :fiber, :pkcal, :pfiber, :protein, :fat, :carbs,
                    :p_protein, :p_fat, :p_carbs)
            ON CONFLICT (recipe_id) DO UPDATE SET
                calories_kcal = EXCLUDED.calories_kcal,
                fiber_g = EXCLUDED.fiber_g,
                per_serving_kcal = EXCLUDED.per_serving_kcal,
                per_serving_fiber_g = EXCLUDED.per_serving_fiber_g,
                protein_g = EXCLUDED.protein_g,
                fat_g = EXCLUDED.fat_g,
                carbs_g = EXCLUDED.carbs_g,
                per_serving_protein_g = EXCLUDED.per_serving_protein_g,
                per_serving_fat_g = EXCLUDED.per_serving_fat_g,
                per_serving_carbs_g = EXCLUDED.per_serving_carbs_g
            """
        ),
        {
            "rid": recipe_id,
            "kcal": calories_kcal,
            "fiber": fiber_g,
            "pkcal": per_serving_kcal,
            "pfiber": per_serving_fiber_g,
            "protein": protein_g,
            "fat": fat_g,
            "carbs": carbs_g,
            "p_protein": per_serving_protein_g,
            "p_fat": per_serving_fat_g,
            "p_carbs": per_serving_carbs_g,
        },
    )


def fetch_enrichment_inputs(
    conn: Connection,
) -> list[tuple[int, str, Decimal | None, Decimal | None]]:
    rows = conn.execute(
        text(
            """
            SELECT ri.recipe_id, ri.ingredient_canonical, ri.per_serving_grams, r.servings_count
            FROM meal_planning.recipe_ingredient ri
            JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
            WHERE ri.ingredient_canonical IS NOT NULL
              AND ri.per_serving_grams IS NOT NULL
              AND ri.sub_recipe_id IS NULL
            """
        )
    ).fetchall()
    return [(int(r[0]), str(r[1]), r[2], r[3]) for r in rows]


def fetch_declared_nutrition(
    conn: Connection,
) -> dict[
    int,
    tuple[
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
    ],
]:
    """Recipes that carry a Paprika Nutrition section. Returns
    {recipe_id: (kcal, protein, fiber, fat, carbs, servings_count)} (all
    per-serving as the recipe states)."""
    rows = conn.execute(
        text(
            """
            SELECT recipe_id, declared_kcal, declared_protein_g, declared_fiber_g,
                   declared_fat_g, declared_carbs_g, servings_count
            FROM meal_planning.recipe
            WHERE declared_kcal IS NOT NULL
               OR declared_protein_g IS NOT NULL
               OR declared_fiber_g IS NOT NULL
               OR declared_fat_g IS NOT NULL
               OR declared_carbs_g IS NOT NULL
            """
        )
    ).fetchall()
    return {int(r[0]): (r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows}


def fetch_sub_recipe_lines(
    conn: Connection,
) -> list[tuple[int, int, Decimal | None]]:
    """Rows where the ingredient line points at another recipe.

    Returns (parent_recipe_id, sub_recipe_id, per_serving_grams_of_parent_line).
    """
    rows = conn.execute(
        text(
            """
            SELECT recipe_id, sub_recipe_id, per_serving_grams
            FROM meal_planning.recipe_ingredient
            WHERE sub_recipe_id IS NOT NULL
            """
        )
    ).fetchall()
    return [(int(r[0]), int(r[1]), r[2]) for r in rows]


def fetch_recipe_serving_grams(conn: Connection) -> dict[int, Decimal]:
    """Total per-serving grams of each recipe (sum of its non-sub ingredient
    per_serving_grams). Used to convert a sub-recipe's per-serving macros into
    a per-gram rate."""
    rows = conn.execute(
        text(
            """
            SELECT recipe_id, COALESCE(SUM(per_serving_grams), 0)
            FROM meal_planning.recipe_ingredient
            WHERE per_serving_grams IS NOT NULL
              AND sub_recipe_id IS NULL
            GROUP BY recipe_id
            """
        )
    ).fetchall()
    return {int(r[0]): Decimal(str(r[1])) for r in rows if r[1] is not None}


def delete_cache(conn: Connection, ingredient_canonical: str) -> None:
    conn.execute(
        text(
            "DELETE FROM meal_planning.ingredient_nutrition_cache WHERE ingredient_canonical = :c"
        ),
        {"c": ingredient_canonical},
    )


def fetch_sample_raw_text(conn: Connection) -> dict[str, str]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT ON (ingredient_canonical) ingredient_canonical, raw_text
            FROM meal_planning.recipe_ingredient
            WHERE ingredient_canonical IS NOT NULL
            ORDER BY ingredient_canonical, raw_text
            """
        )
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}
