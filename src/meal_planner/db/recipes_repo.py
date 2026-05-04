from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, text


def upsert_recipe(
    conn: Connection,
    *,
    title: str,
    rating: Decimal | None,
    servings: str | None,
    servings_count: Decimal | None,
    difficulty: str | None,
    categories: str | None,
    source: str | None,
    last_modified: datetime | None,
    is_plant_based: bool,
) -> int:
    row = conn.execute(
        text(
            """
            INSERT INTO meal_planning.recipe
                (title, rating, servings, servings_count, difficulty, categories,
                 source, last_modified, is_plant_based)
            VALUES (:title, :rating, :servings, :servings_count, :difficulty, :categories,
                    :source, :last_modified, :is_plant_based)
            ON CONFLICT (title) DO UPDATE SET
                rating = EXCLUDED.rating,
                servings = EXCLUDED.servings,
                servings_count = EXCLUDED.servings_count,
                difficulty = EXCLUDED.difficulty,
                categories = EXCLUDED.categories,
                source = EXCLUDED.source,
                last_modified = EXCLUDED.last_modified,
                is_plant_based = EXCLUDED.is_plant_based
            RETURNING recipe_id
            """
        ),
        {
            "title": title,
            "rating": rating,
            "servings": servings,
            "servings_count": servings_count,
            "difficulty": difficulty,
            "categories": categories,
            "source": source,
            "last_modified": last_modified,
            "is_plant_based": is_plant_based,
        },
    ).scalar_one()
    return int(row)


def upsert_recipe_source(
    conn: Connection, *, recipe_id: int, source_path: str, raw_html: str
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.recipe_source (recipe_id, source_path, raw_html, ingested_at)
            VALUES (:recipe_id, :source_path, :raw_html, now())
            ON CONFLICT (recipe_id, source_path) DO UPDATE SET
                raw_html = EXCLUDED.raw_html,
                ingested_at = now()
            """
        ),
        {"recipe_id": recipe_id, "source_path": source_path, "raw_html": raw_html},
    )


def replace_meal_types(conn: Connection, *, recipe_id: int, meal_types: list[str]) -> None:
    conn.execute(
        text("DELETE FROM meal_planning.recipe_meal_type WHERE recipe_id = :rid"),
        {"rid": recipe_id},
    )
    for mt in meal_types:
        conn.execute(
            text(
                """
                INSERT INTO meal_planning.recipe_meal_type (recipe_id, meal_type)
                VALUES (:rid, :mt) ON CONFLICT DO NOTHING
                """
            ),
            {"rid": recipe_id, "mt": mt},
        )


def insert_raw_ingredient_lines(conn: Connection, *, recipe_id: int, lines: list[str]) -> int:
    inserted = 0
    for line in lines:
        result = conn.execute(
            text(
                """
                INSERT INTO meal_planning.recipe_ingredient (recipe_id, raw_text)
                VALUES (:rid, :raw)
                ON CONFLICT (recipe_id, raw_text) DO NOTHING
                """
            ),
            {"rid": recipe_id, "raw": line},
        )
        inserted += result.rowcount or 0
    return inserted
