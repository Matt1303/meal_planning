from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class ParseUpdate:
    recipe_id: int
    raw_text: str
    ingredient_name: str | None
    ingredient_canonical: str | None
    quantity_value: Decimal | None
    quantity_unit: str | None
    quantity_grams: Decimal | None
    per_serving_grams: Decimal | None
    food_group: str | None
    portions: Decimal | None
    portion_met: bool | None


@dataclass(frozen=True)
class CachedParse:
    ingredient_name: str | None
    ingredient_canonical: str | None
    quantity_value: Decimal | None
    quantity_unit: str | None
    quantity_grams: Decimal | None
    food_group: str | None


def fetch_overrides(conn: Connection) -> dict[str, tuple[str | None, str | None]]:
    rows = conn.execute(
        text(
            "SELECT raw_text, ingredient_canonical, food_group FROM meal_planning.ingredient_override"
        )
    ).fetchall()
    out: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        raw_text = row[0]
        if raw_text is None:
            continue
        out[str(raw_text).strip().lower()] = (row[1], row[2])
    return out


def fetch_unparsed_rows(conn: Connection) -> list[tuple[int, str, Decimal | None]]:
    rows = conn.execute(
        text(
            """
            SELECT ri.recipe_id, ri.raw_text, r.servings_count
            FROM meal_planning.recipe_ingredient ri
            JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
            WHERE ri.ingredient_canonical IS NULL
               OR ri.quantity_grams IS NULL
               OR ri.per_serving_grams IS NULL
               OR ri.food_group IS NULL
            """
        )
    ).fetchall()
    return [(int(r[0]), str(r[1]), r[2]) for r in rows]


def fetch_cache(conn: Connection, raw_text: str) -> CachedParse | None:
    row = conn.execute(
        text(
            """
            SELECT ingredient_name, ingredient_canonical, quantity_value, quantity_unit,
                   quantity_grams, food_group
            FROM meal_planning.ingredient_parse_cache
            WHERE raw_text = :raw
            """
        ),
        {"raw": raw_text},
    ).fetchone()
    if row is None:
        return None
    return CachedParse(
        ingredient_name=row[0],
        ingredient_canonical=row[1],
        quantity_value=row[2],
        quantity_unit=row[3],
        quantity_grams=row[4],
        food_group=row[5],
    )


def write_parse_update(conn: Connection, update: ParseUpdate) -> None:
    conn.execute(
        text(
            """
            UPDATE meal_planning.recipe_ingredient
            SET ingredient_name = :ingredient_name,
                ingredient_canonical = :ingredient_canonical,
                quantity_value = :quantity_value,
                quantity_unit = :quantity_unit,
                quantity_grams = :quantity_grams,
                per_serving_grams = :per_serving_grams,
                food_group = :food_group,
                portions = :portions,
                portion_met = :portion_met
            WHERE recipe_id = :recipe_id AND raw_text = :raw_text
            """
        ),
        {
            "recipe_id": update.recipe_id,
            "raw_text": update.raw_text,
            "ingredient_name": update.ingredient_name,
            "ingredient_canonical": update.ingredient_canonical,
            "quantity_value": update.quantity_value,
            "quantity_unit": update.quantity_unit,
            "quantity_grams": update.quantity_grams,
            "per_serving_grams": update.per_serving_grams,
            "food_group": update.food_group,
            "portions": update.portions,
            "portion_met": update.portion_met,
        },
    )

    if update.ingredient_name:
        conn.execute(
            text(
                """
                INSERT INTO meal_planning.ingredient_parse_cache
                    (raw_text, ingredient_name, ingredient_canonical, quantity_value,
                     quantity_unit, quantity_grams, food_group, updated_at)
                VALUES (:raw_text, :ingredient_name, :ingredient_canonical, :quantity_value,
                        :quantity_unit, :quantity_grams, :food_group, now())
                ON CONFLICT (raw_text) DO UPDATE SET
                    ingredient_name = EXCLUDED.ingredient_name,
                    ingredient_canonical = EXCLUDED.ingredient_canonical,
                    quantity_value = EXCLUDED.quantity_value,
                    quantity_unit = EXCLUDED.quantity_unit,
                    quantity_grams = EXCLUDED.quantity_grams,
                    food_group = EXCLUDED.food_group,
                    updated_at = now()
                """
            ),
            {
                "raw_text": update.raw_text,
                "ingredient_name": update.ingredient_name,
                "ingredient_canonical": update.ingredient_canonical,
                "quantity_value": update.quantity_value,
                "quantity_unit": update.quantity_unit,
                "quantity_grams": update.quantity_grams,
                "food_group": update.food_group,
            },
        )


def upsert_override(
    conn: Connection,
    *,
    raw_text: str,
    canonical: str | None,
    food_group: str | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.ingredient_override
                (raw_text, ingredient_canonical, food_group, updated_at)
            VALUES (:raw, :can, :fg, now())
            ON CONFLICT (raw_text) DO UPDATE SET
                ingredient_canonical = EXCLUDED.ingredient_canonical,
                food_group = EXCLUDED.food_group,
                updated_at = now()
            """
        ),
        {"raw": raw_text.strip().lower(), "can": canonical, "fg": food_group},
    )


def list_overrides(conn: Connection) -> list[tuple[str, str | None, str | None]]:
    rows = conn.execute(
        text(
            "SELECT raw_text, ingredient_canonical, food_group "
            "FROM meal_planning.ingredient_override ORDER BY raw_text"
        )
    ).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def delete_override(conn: Connection, raw_text: str) -> int:
    result = conn.execute(
        text("DELETE FROM meal_planning.ingredient_override WHERE raw_text = :raw"),
        {"raw": raw_text.strip().lower()},
    )
    return result.rowcount or 0


def fetch_recent_for_summary(conn: Connection, limit: int = 50) -> Sequence[tuple[int, str | None]]:
    rows = conn.execute(
        text("SELECT recipe_id, title FROM meal_planning.recipe ORDER BY recipe_id DESC LIMIT :n"),
        {"n": limit},
    ).fetchall()
    return [(int(r[0]), r[1]) for r in rows]
