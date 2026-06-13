"""Export ingredients needing manual nutrition/grams input.

Writes two CSVs under data/nutrition_gaps/:
  - missing.csv          : ingredients contributing 0 kcal (no nutrition match
                           and/or no grams parsed) for you to fill in.
  - low_confidence.csv   : matches the pipeline is unsure about, to review.

Run:  DB_HOST=localhost python scripts/export_nutrition_gaps.py
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import text

from meal_planner.db import get_engine

OUT = Path("data/nutrition_gaps")

# Lines that are headers / trivial seasonings — not worth manual nutrition.
_NOISE = (
    r"(:|to taste|to serve|for serving|to garnish|^garnish|optional\)?$|"
    r"^a pinch|pinch of|^a handful|handful of|^a drizzle|drizzle of|"
    r"^a splash|splash of|season to|^salt|^sea salt|^black pepper|"
    r"^freshly ground|^water$|^cold water|^boiling water|^ice\b)"
)

# Canonicals that are nutritionally trivial (used in tsp/tbsp amounts) — a few
# kcal at most, not worth hand-entering. Excluded from the missing list.
_TRIVIAL_CANONICALS = frozenset(
    {
        "salt",
        "sea salt",
        "black pepper",
        "pepper",
        "water",
        "cumin",
        "paprika",
        "cinnamon",
        "ground turmeric",
        "turmeric",
        "oregano",
        "nutmeg",
        "fennel",
        "chilli flakes",
        "chilli powder",
        "chilli peppers",
        "ancho chili powder",
        "chili powder",
        "garam masala",
        "bay leaf",
        "bay leaves",
        "coriander",
        "cardamom",
        "cloves",
        "allspice",
        "cayenne",
        "garlic powder",
        "onion powder",
        "mustard powder",
        "baking powder",
        "baking soda",
        "bicarbonate of soda",
        "cream of tartar",
        "vanilla extract",
        "almond extract",
    }
)


def export_missing(conn: object) -> int:
    rows = conn.execute(  # type: ignore[attr-defined]
        text(
            """
            WITH agg AS (
                SELECT
                    ri.ingredient_canonical,
                    count(DISTINCT ri.recipe_id) AS recipe_count,
                    bool_or(ri.per_serving_grams IS NOT NULL) AS has_grams,
                    min(ri.raw_text) AS sample_raw_text
                FROM meal_planning.recipe_ingredient ri
                JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
                WHERE ri.ingredient_canonical IS NOT NULL
                  AND ri.sub_recipe_id IS NULL
                  AND r.is_plant_based = TRUE
                  AND ri.raw_text !~* :noise
                  AND ri.raw_text NOT ILIKE '%(separate recipe%'
                  AND ri.raw_text NOT ILIKE '%(see recipe%'
                GROUP BY ri.ingredient_canonical
            )
            SELECT a.ingredient_canonical, a.sample_raw_text, a.recipe_count,
                   a.has_grams, c.kcal_per_100g
            FROM agg a
            LEFT JOIN meal_planning.ingredient_nutrition_cache c
                   ON c.ingredient_canonical = a.ingredient_canonical
            WHERE c.kcal_per_100g IS NULL OR c.kcal_per_100g = 0
               OR a.has_grams = FALSE
            ORDER BY
                -- actionable "has grams but no nutrition" first
                (a.has_grams AND (c.kcal_per_100g IS NULL OR c.kcal_per_100g = 0)) DESC,
                a.recipe_count DESC, a.ingredient_canonical
            """
        ),
        {"noise": _NOISE},
    ).fetchall()

    path = OUT / "missing.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ingredient_canonical",
                "sample_raw_text",
                "recipe_count",
                "issue",
                "grams_per_piece",  # fill if the line is count-based (e.g. "4 tortillas")
                "kcal_per_100g",
                "protein_g_per_100g",
                "fiber_g_per_100g",
                "fat_g_per_100g",
                "carbs_g_per_100g",
            ]
        )
        written = 0
        for canonical, sample, recipe_count, has_grams, kcal in rows:
            if canonical in _TRIVIAL_CANONICALS:
                continue
            has_nutrition = kcal is not None and kcal != 0
            if not has_nutrition and not has_grams:
                issue = "no nutrition + no grams"
            elif not has_nutrition:
                issue = "no nutrition match"
            else:
                issue = "no grams parsed"
            w.writerow([canonical, sample, recipe_count, issue, "", "", "", "", "", ""])
            written += 1
    return written


def export_low_confidence(conn: object) -> int:
    rows = conn.execute(  # type: ignore[attr-defined]
        text(
            """
            WITH usage AS (
                SELECT ingredient_canonical,
                       count(DISTINCT recipe_id) AS recipe_count,
                       min(raw_text) AS sample_raw_text
                FROM meal_planning.recipe_ingredient
                WHERE ingredient_canonical IS NOT NULL AND sub_recipe_id IS NULL
                GROUP BY ingredient_canonical
            )
            SELECT c.ingredient_canonical, u.sample_raw_text, u.recipe_count,
                   c.source, c.match_source_name, c.match_score,
                   c.kcal_per_100g, c.protein_g_per_100g, c.fiber_g_per_100g,
                   c.fat_g_per_100g, c.carbs_g_per_100g
            FROM meal_planning.ingredient_nutrition_cache c
            JOIN usage u ON u.ingredient_canonical = c.ingredient_canonical
            WHERE c.source IN ('claude_low', 'claude_medium')
               OR (c.match_score IS NOT NULL AND c.match_score < 90
                   AND c.source NOT LIKE 'claude%')
            ORDER BY u.recipe_count DESC, c.ingredient_canonical
            """
        )
    ).fetchall()

    path = OUT / "low_confidence.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ingredient_canonical",
                "sample_raw_text",
                "recipe_count",
                "current_source",
                "current_match",
                "match_score",
                "current_kcal_per_100g",
                "current_protein_g_per_100g",
                "current_fiber_g_per_100g",
                "corrected_kcal_per_100g",  # fill only to override
                "corrected_protein_g_per_100g",
                "corrected_fiber_g_per_100g",
                "corrected_fat_g_per_100g",
                "corrected_carbs_g_per_100g",
            ]
        )
        for r in rows:
            w.writerow([*list(r), "", "", "", "", ""])
    return len(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    with engine.connect() as conn:
        n_missing = export_missing(conn)
        n_low = export_low_confidence(conn)
    print(f"missing.csv: {n_missing} rows")
    print(f"low_confidence.csv: {n_low} rows")


if __name__ == "__main__":
    main()
