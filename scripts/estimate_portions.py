"""Estimate per-portion gram weights for no-quantity ingredients via the LLM.

Finds ingredients that were added to a recipe without any quantity, that resolved
to a real food but aren't yet in config/default_portion_grams.csv, asks Claude
for a typical per-portion weight, and appends the estimates to that CSV (note
"LLM estimate") so they become curated, reviewable defaults.

Run:  DB_HOST=localhost python scripts/estimate_portions.py
Then re-run the pipeline (or parse + enrich) to apply.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import text

from meal_planner.config import Settings
from meal_planner.db import get_engine
from meal_planner.llm import get_llm_client
from meal_planner.llm.base import NullLLM, NutritionQuery

CSV_PATH = Path("config/default_portion_grams.csv")
_NOISE = (
    r"(:|to taste|to serve|garnish|^salt|^pepper|pinch|^a |^for |^made from|"
    r"\(separate recipe|^[A-Z][A-Z ]+$)"
)


def main() -> None:
    settings = Settings.load("config/pipeline.yaml")
    llm = get_llm_client(settings.llm)
    if isinstance(llm, NullLLM):
        print("No LLM configured (set ANTHROPIC_API_KEY); nothing to do.")
        return

    existing = {
        line.split(",", 1)[0].strip().lower()
        for line in CSV_PATH.read_text().splitlines()[1:]
        if line.strip()
    }

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ri.ingredient_canonical, min(ri.raw_text)
                FROM meal_planning.recipe_ingredient ri
                JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
                WHERE r.is_plant_based AND ri.sub_recipe_id IS NULL
                  AND ri.ingredient_canonical IS NOT NULL
                  AND ri.per_serving_grams IS NULL
                  AND ri.raw_text !~ '[0-9]'
                  AND ri.raw_text !~* :noise
                GROUP BY ri.ingredient_canonical
                ORDER BY 1
                """
            ),
            {"noise": _NOISE},
        ).fetchall()

    queries = [
        NutritionQuery(ingredient_canonical=str(c), sample_raw_text=str(s))
        for c, s in rows
        if str(c).strip().lower() not in existing
    ]
    if not queries:
        print("No new no-quantity ingredients to estimate.")
        return

    print(f"Estimating portions for {len(queries)} ingredients…")
    estimates = llm.estimate_portions(queries)
    appended = 0
    with CSV_PATH.open("a", newline="") as fh:
        writer = csv.writer(fh)
        for est in estimates:
            if est.grams_per_portion is None or est.grams_per_portion <= 0:
                continue
            if est.ingredient_canonical.strip().lower() in existing:
                continue
            writer.writerow(
                [
                    est.ingredient_canonical,
                    round(float(est.grams_per_portion)),
                    f"LLM estimate{f' — {est.note}' if est.note else ''}",
                ]
            )
            appended += 1
    print(f"Appended {appended} estimates to {CSV_PATH}. Review, then re-run parse + enrich.")


if __name__ == "__main__":
    main()
