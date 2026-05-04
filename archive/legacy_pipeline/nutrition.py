import os
import json
import pandas as pd
import requests
from rapidfuzz import process
from sqlalchemy import text
from .db import get_engine
from .config import load_config


def _load_cofid(path):
    if not os.path.exists(path):
        return None
    if path.lower().endswith(".xlsx") or path.lower().endswith(".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    return df


def _guess_columns(df):
    cols = {c.lower(): c for c in df.columns}
    name_col = None
    kcal_col = None
    fiber_col = None
    for key, col in cols.items():
        if "food name" in key or key == "foodname" or key == "name":
            name_col = col
        if "energy" in key and "kcal" in key:
            kcal_col = col
        if "fibre" in key or "fiber" in key:
            fiber_col = col
    return name_col, kcal_col, fiber_col


def _lookup_cofid(df, ingredient):
    name_col, kcal_col, fiber_col = _guess_columns(df)
    if not name_col:
        return None
    names = df[name_col].astype(str).str.lower().tolist()
    match = process.extractOne(ingredient.lower(), names)
    if not match:
        return None
    name_match, score, idx = match
    if score < 80:
        return None
    row = df.iloc[idx]
    kcal = float(row[kcal_col]) if kcal_col and pd.notna(row[kcal_col]) else None
    fiber = float(row[fiber_col]) if fiber_col and pd.notna(row[fiber_col]) else None
    return {"kcal_per_100g": kcal, "fiber_g_per_100g": fiber, "source": "cofid"}


def _lookup_usda(api_key, ingredient):
    if not api_key:
        return None
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"api_key": api_key, "query": ingredient, "pageSize": 1}
    resp = requests.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        return None
    data = resp.json()
    foods = data.get("foods", [])
    if not foods:
        return None
    nutrients = {n["nutrientName"].lower(): n.get("value") for n in foods[0].get("foodNutrients", [])}
    kcal = nutrients.get("energy")
    fiber = nutrients.get("fiber, total dietary") or nutrients.get("dietary fiber")
    return {"kcal_per_100g": kcal, "fiber_g_per_100g": fiber, "source": "usda"}


def lookup_nutrition(ingredient, df, api_key, engine):
    cached = engine.execute(
        text("SELECT kcal_per_100g, fiber_g_per_100g, source FROM meal_planning.ingredient_nutrition_cache WHERE ingredient_canonical = :ing"),
        {"ing": ingredient},
    ).fetchone()
    if cached:
        return {"kcal_per_100g": cached[0], "fiber_g_per_100g": cached[1], "source": cached[2]}

    result = None
    if df is not None:
        result = _lookup_cofid(df, ingredient)
    if result is None:
        result = _lookup_usda(api_key, ingredient)

    if result:
        engine.execute(
            text(
                """
                INSERT INTO meal_planning.ingredient_nutrition_cache (ingredient_canonical, kcal_per_100g, fiber_g_per_100g, source)
                VALUES (:ingredient, :kcal, :fiber, :source)
                ON CONFLICT (ingredient_canonical) DO UPDATE SET
                    kcal_per_100g = EXCLUDED.kcal_per_100g,
                    fiber_g_per_100g = EXCLUDED.fiber_g_per_100g,
                    source = EXCLUDED.source,
                    updated_at = now()
                """
            ),
            {
                "ingredient": ingredient,
                "kcal": result.get("kcal_per_100g"),
                "fiber": result.get("fiber_g_per_100g"),
                "source": result.get("source"),
            },
        )
    return result


def enrich_nutrition(config_path=None):
    cfg = load_config(config_path)
    nutrition_cfg = cfg.get("nutrition", {})
    cofid_path = nutrition_cfg.get("cofid_path")
    cofid_url = nutrition_cfg.get("cofid_url")
    usda_api_key = nutrition_cfg.get("usda_api_key") or os.getenv("USDA_API_KEY")

    if cofid_path and not os.path.exists(cofid_path) and cofid_url:
        os.makedirs(os.path.dirname(cofid_path), exist_ok=True)
        resp = requests.get(cofid_url, timeout=60)
        resp.raise_for_status()
        with open(cofid_path, "wb") as f:
            f.write(resp.content)

    df = _load_cofid(cofid_path) if cofid_path else None
    engine = get_engine()

    query = """
        SELECT ri.recipe_id, ri.ingredient_canonical, ri.per_serving_grams, r.servings_count
        FROM meal_planning.recipe_ingredient ri
        JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
        WHERE ri.ingredient_canonical IS NOT NULL AND ri.per_serving_grams IS NOT NULL
    """

    with engine.begin() as conn:
        rows = conn.execute(text(query)).fetchall()
        recipes = {}
        total_items = 0
        covered_items = 0
        for recipe_id, canonical, per_serving_grams, servings_count in rows:
            total_items += 1
            if canonical is None:
                continue
            rec = recipes.setdefault(recipe_id, {"servings_count": servings_count, "kcal": 0.0, "fiber": 0.0})
            result = lookup_nutrition(canonical, df, usda_api_key, conn)
            if not result:
                continue
            covered_items += 1
            kcal = result.get("kcal_per_100g") or 0.0
            fiber = result.get("fiber_g_per_100g") or 0.0
            rec["kcal"] += kcal * float(per_serving_grams) / 100.0
            rec["fiber"] += fiber * float(per_serving_grams) / 100.0

        for recipe_id, data in recipes.items():
            servings_count = data.get("servings_count") or 1
            per_serving_kcal = data["kcal"]
            per_serving_fiber = data["fiber"]
            total_kcal = per_serving_kcal * float(servings_count)
            total_fiber = per_serving_fiber * float(servings_count)

            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.recipe_nutrition (recipe_id, calories_kcal, fiber_g, per_serving_kcal, per_serving_fiber_g)
                    VALUES (:recipe_id, :kcal, :fiber, :per_kcal, :per_fiber)
                    ON CONFLICT (recipe_id) DO UPDATE SET
                        calories_kcal = EXCLUDED.calories_kcal,
                        fiber_g = EXCLUDED.fiber_g,
                        per_serving_kcal = EXCLUDED.per_serving_kcal,
                        per_serving_fiber_g = EXCLUDED.per_serving_fiber_g
                    """
                ),
                {
                    "recipe_id": recipe_id,
                    "kcal": total_kcal,
                    "fiber": total_fiber,
                    "per_kcal": per_serving_kcal,
                    "per_fiber": per_serving_fiber,
                },
            )

        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "nutrition_items_total", "value": total_items},
        )
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "nutrition_items_covered", "value": covered_items},
        )
        coverage = (covered_items / total_items) if total_items else 0.0
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "nutrition_coverage_ratio", "value": coverage},
        )

    return len(recipes)
