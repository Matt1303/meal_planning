import os
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from sqlalchemy import text
from .db import get_engine, wait_for_db
from .config import load_config


def _parse_servings_count(text_value):
    if not text_value:
        return None
    txt = text_value.strip()
    nums = []
    current = ""
    for ch in txt:
        if ch.isdigit() or ch == ".":
            current += ch
        else:
            if current:
                nums.append(current)
                current = ""
    if current:
        nums.append(current)
    values = []
    for n in nums:
        try:
            values.append(float(n))
        except ValueError:
            pass
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return sum(values[:2]) / 2.0


def _extract_text(soup, selector):
    el = soup.select_one(selector) if selector else None
    return el.get_text(strip=True) if el else ""


def _extract_attr(soup, selector, attr):
    el = soup.select_one(selector) if selector else None
    return el.get(attr, "") if el else ""


def ingest_local_html(config_path=None):
    cfg = load_config(config_path)
    source = cfg.get("sources", {}).get("local_html", {})
    base_path = source.get("path")
    selectors = source.get("selectors", {})
    if not base_path or not os.path.exists(base_path):
        raise FileNotFoundError(f"Recipe path not found: {base_path}")

    engine = get_engine()
    if not wait_for_db(engine):
        raise RuntimeError("Database not ready")

    html_files = [f for f in os.listdir(base_path) if f.lower().endswith(".html")]
    html_files.sort()

    recipe_count = 0
    ingredient_count = 0

    with engine.begin() as conn:
        for fname in html_files:
            fpath = os.path.join(base_path, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                raw_html = f.read()

            soup = BeautifulSoup(raw_html, "html.parser")
            title = _extract_text(soup, selectors.get("title"))
            categories = _extract_text(soup, selectors.get("category"))
            rating = _extract_attr(soup, selectors.get("rating"), "value")
            servings = _extract_text(soup, selectors.get("servings"))
            difficulty = _extract_text(soup, selectors.get("difficulty"))

            try:
                rating_val = float(rating) if rating else None
            except ValueError:
                rating_val = None

            servings_count = _parse_servings_count(servings)
            last_modified = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)

            upsert_recipe = text(
                """
                INSERT INTO meal_planning.recipe (title, rating, servings, servings_count, difficulty, categories, source, last_modified)
                VALUES (:title, :rating, :servings, :servings_count, :difficulty, :categories, :source, :last_modified)
                ON CONFLICT (title) DO UPDATE SET
                    rating = EXCLUDED.rating,
                    servings = EXCLUDED.servings,
                    servings_count = EXCLUDED.servings_count,
                    difficulty = EXCLUDED.difficulty,
                    categories = EXCLUDED.categories,
                    source = EXCLUDED.source,
                    last_modified = EXCLUDED.last_modified
                RETURNING recipe_id
                """
            )
            recipe_id = conn.execute(
                upsert_recipe,
                {
                    "title": title,
                    "rating": rating_val,
                    "servings": servings,
                    "servings_count": servings_count,
                    "difficulty": difficulty,
                    "categories": categories,
                    "source": fpath,
                    "last_modified": last_modified,
                },
            ).scalar()

            recipe_count += 1

            conn.execute(
                text(
                    "INSERT INTO meal_planning.recipe_source (recipe_id, source_path, raw_html) VALUES (:recipe_id, :source_path, :raw_html)"
                ),
                {"recipe_id": recipe_id, "source_path": fpath, "raw_html": raw_html},
            )

            conn.execute(text("DELETE FROM meal_planning.recipe_meal_type WHERE recipe_id = :recipe_id"), {"recipe_id": recipe_id})

            meal_types = []
            if categories:
                for part in categories.split(","):
                    mt = part.strip().lower()
                    if mt.endswith("s"):
                        mt = mt[:-1]
                    if mt:
                        meal_types.append(mt)

            for mt in set(meal_types):
                conn.execute(
                    text("INSERT INTO meal_planning.recipe_meal_type (recipe_id, meal_type) VALUES (:recipe_id, :meal_type) ON CONFLICT DO NOTHING"),
                    {"recipe_id": recipe_id, "meal_type": mt},
                )

            ingredient_lines = []
            for p in soup.select(selectors.get("ingredient_lines", "")):
                txt = p.get_text(separator=" ", strip=True)
                if txt:
                    ingredient_lines.append(txt)

            for line in ingredient_lines:
                conn.execute(
                    text(
                        """
                        INSERT INTO meal_planning.recipe_ingredient (recipe_id, raw_text)
                        VALUES (:recipe_id, :raw_text)
                        ON CONFLICT (recipe_id, raw_text) DO NOTHING
                        """
                    ),
                    {"recipe_id": recipe_id, "raw_text": line},
                )
                ingredient_count += 1

        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "ingest_recipes", "value": recipe_count},
        )
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "ingest_ingredients", "value": ingredient_count},
        )

    return len(html_files)
