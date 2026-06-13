"""Load manually-filled nutrition values back into Postgres.

Reads the two CSVs under data/nutrition_gaps/ after you've filled them in:
  - missing.csv         : rows where you entered kcal_per_100g (+ macros) are
                          upserted into ingredient_nutrition_cache as source
                          'manual' (match_score 100, so they're trusted and not
                          re-verified). grams_per_piece values are appended to
                          config/piece_grams.csv.
  - low_confidence.csv  : rows where you entered any corrected_* value override
                          the cached entry (source 'manual').

Only filled-in rows are touched; blanks are ignored. Re-runnable.

Run:  DB_HOST=localhost python scripts/load_nutrition_gaps.py
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text

from meal_planner.db import get_engine

OUT = Path("data/nutrition_gaps")
PIECE_GRAMS = Path("config/piece_grams.csv")


def _dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


_UPSERT = text(
    """
    INSERT INTO meal_planning.ingredient_nutrition_cache
        (ingredient_canonical, kcal_per_100g, fiber_g_per_100g, protein_g_per_100g,
         fat_g_per_100g, carbs_g_per_100g, source, match_score, match_source_name, updated_at)
    VALUES (:ing, :kcal, :fiber, :protein, :fat, :carbs,
            'manual', 100, 'manual entry', now())
    ON CONFLICT (ingredient_canonical) DO UPDATE SET
        kcal_per_100g = EXCLUDED.kcal_per_100g,
        fiber_g_per_100g = EXCLUDED.fiber_g_per_100g,
        protein_g_per_100g = EXCLUDED.protein_g_per_100g,
        fat_g_per_100g = EXCLUDED.fat_g_per_100g,
        carbs_g_per_100g = EXCLUDED.carbs_g_per_100g,
        source = 'manual',
        match_score = 100,
        match_source_name = 'manual entry',
        updated_at = now()
    """
)


def load_missing() -> tuple[int, list[tuple[str, str]]]:
    path = OUT / "missing.csv"
    if not path.exists():
        return 0, []
    engine = get_engine()
    nutrition_loaded = 0
    piece_rows: list[tuple[str, str]] = []
    with engine.begin() as conn, path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("ingredient_canonical") or "").strip()
            if not canonical:
                continue
            kcal = _dec(row.get("kcal_per_100g"))
            if kcal is not None:
                conn.execute(
                    _UPSERT,
                    {
                        "ing": canonical,
                        "kcal": kcal,
                        "fiber": _dec(row.get("fiber_g_per_100g")),
                        "protein": _dec(row.get("protein_g_per_100g")),
                        "fat": _dec(row.get("fat_g_per_100g")),
                        "carbs": _dec(row.get("carbs_g_per_100g")),
                    },
                )
                nutrition_loaded += 1
            grams = _dec(row.get("grams_per_piece"))
            if grams is not None:
                piece_rows.append((canonical, str(grams)))
    return nutrition_loaded, piece_rows


def append_piece_grams(piece_rows: list[tuple[str, str]]) -> int:
    if not piece_rows or not PIECE_GRAMS.exists():
        return 0
    existing = {
        line.split(",", 1)[0].strip().lower()
        for line in PIECE_GRAMS.read_text().splitlines()[1:]
        if line.strip()
    }
    added = 0
    with PIECE_GRAMS.open("a", newline="") as fh:
        writer = csv.writer(fh)
        for canonical, grams in piece_rows:
            if canonical.lower() in existing:
                continue
            writer.writerow([canonical, grams, "manual (nutrition gaps)"])
            added += 1
    return added


def load_low_confidence() -> int:
    path = OUT / "low_confidence.csv"
    if not path.exists():
        return 0
    engine = get_engine()
    updated = 0
    with engine.begin() as conn, path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("ingredient_canonical") or "").strip()
            kcal = _dec(row.get("corrected_kcal_per_100g"))
            if not canonical or kcal is None:
                continue
            conn.execute(
                _UPSERT,
                {
                    "ing": canonical,
                    "kcal": kcal,
                    "fiber": _dec(row.get("corrected_fiber_g_per_100g")),
                    "protein": _dec(row.get("corrected_protein_g_per_100g")),
                    "fat": _dec(row.get("corrected_fat_g_per_100g")),
                    "carbs": _dec(row.get("corrected_carbs_g_per_100g")),
                },
            )
            updated += 1
    return updated


def main() -> None:
    nutrition_loaded, piece_rows = load_missing()
    piece_added = append_piece_grams(piece_rows)
    low_updated = load_low_confidence()
    print(f"missing.csv: {nutrition_loaded} nutrition rows upserted")
    print(f"piece_grams.csv: {piece_added} per-piece weights appended")
    print(f"low_confidence.csv: {low_updated} corrections applied")
    print("Re-run the pipeline (or just nutrition enrich) to apply.")


if __name__ == "__main__":
    main()
