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


def fetch_cache(conn: Connection, ingredient_canonical: str) -> CachedNutrition | None:
    row = conn.execute(
        text(
            """
            SELECT kcal_per_100g, fiber_g_per_100g, protein_g_per_100g,
                   fat_g_per_100g, carbs_g_per_100g, source
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
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.recipe_nutrition
                (recipe_id, calories_kcal, fiber_g, per_serving_kcal, per_serving_fiber_g,
                 protein_g, fat_g, carbs_g)
            VALUES (:rid, :kcal, :fiber, :pkcal, :pfiber, :protein, :fat, :carbs)
            ON CONFLICT (recipe_id) DO UPDATE SET
                calories_kcal = EXCLUDED.calories_kcal,
                fiber_g = EXCLUDED.fiber_g,
                per_serving_kcal = EXCLUDED.per_serving_kcal,
                per_serving_fiber_g = EXCLUDED.per_serving_fiber_g,
                protein_g = EXCLUDED.protein_g,
                fat_g = EXCLUDED.fat_g,
                carbs_g = EXCLUDED.carbs_g
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
            """
        )
    ).fetchall()
    return [(int(r[0]), str(r[1]), r[2], r[3]) for r in rows]
