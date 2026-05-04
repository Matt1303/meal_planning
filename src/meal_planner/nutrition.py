from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from rapidfuzz import process
from sqlalchemy import Connection, Engine

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.nutrition_repo import (
    CachedNutrition,
    fetch_cache,
    fetch_enrichment_inputs,
    upsert_cache,
    upsert_recipe_nutrition,
)
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName

log = get_logger(__name__)


@dataclass(frozen=True)
class NutritionResult:
    kcal_per_100g: Decimal | None
    fiber_g_per_100g: Decimal | None
    protein_g_per_100g: Decimal | None
    fat_g_per_100g: Decimal | None
    carbs_g_per_100g: Decimal | None
    source: str | None
    match_score: Decimal | None
    match_source_name: str | None


def _coverage_failure_message(coverage: float, threshold: float) -> str:
    return (
        f"nutrition coverage {coverage:.2%} is below threshold {threshold:.2%}; "
        "ship a CoFID file or set USDA_API_KEY."
    )


def _clean_for_fuzzy(text: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", " ", text.lower())
    cleaned = re.sub(r"\b(raw|cooked|dried|chopped|diced|sliced|fresh|frozen)\b", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _load_cofid(path: Path | None) -> pd.DataFrame | None:
    if not path or not path.exists():
        return None
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    return pd.read_csv(path)


def _guess_columns(df: pd.DataFrame) -> tuple[str | None, dict[str, str]]:
    cols = {str(c).lower(): str(c) for c in df.columns}
    name_col: str | None = None
    nutrient_cols: dict[str, str] = {}
    for key, original in cols.items():
        if name_col is None and ("food name" in key or key == "foodname" or key == "name"):
            name_col = original
        if "kcal" in key and "kcal" not in nutrient_cols:
            nutrient_cols["kcal"] = original
        if ("fibre" in key or "fiber" in key) and "fiber" not in nutrient_cols:
            nutrient_cols["fiber"] = original
        if "protein" in key and "protein" not in nutrient_cols:
            nutrient_cols["protein"] = original
        if "fat" in key and "fat" not in nutrient_cols:
            nutrient_cols["fat"] = original
        if "carbohydrate" in key and "carbs" not in nutrient_cols:
            nutrient_cols["carbs"] = original
    return name_col, nutrient_cols


def _row_value(row: pd.Series, col: str | None) -> Decimal | None:
    if not col:
        return None
    value = row.get(col)
    if value is None or pd.isna(value):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _lookup_cofid(df: pd.DataFrame, ingredient: str) -> NutritionResult | None:
    name_col, nutrient_cols = _guess_columns(df)
    if not name_col:
        return None
    haystack = df[name_col].astype(str).str.lower().tolist()
    cleaned = _clean_for_fuzzy(ingredient)
    match = process.extractOne(cleaned, haystack)
    if not match:
        return None
    name_match, score, idx = match
    score_value = float(score or 0)
    if score_value < 80:
        return None
    row = df.iloc[int(idx)]
    return NutritionResult(
        kcal_per_100g=_row_value(row, nutrient_cols.get("kcal")),
        fiber_g_per_100g=_row_value(row, nutrient_cols.get("fiber")),
        protein_g_per_100g=_row_value(row, nutrient_cols.get("protein")),
        fat_g_per_100g=_row_value(row, nutrient_cols.get("fat")),
        carbs_g_per_100g=_row_value(row, nutrient_cols.get("carbs")),
        source="cofid",
        match_score=Decimal(str(score_value)),
        match_source_name=str(name_match),
    )


def _lookup_usda(api_key: str, ingredient: str) -> NutritionResult | None:
    if not api_key:
        return None
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params: dict[str, Any] = {
        "api_key": api_key,
        "query": ingredient,
        "pageSize": 1,
        "dataType": "Foundation,SR Legacy",
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            last_error = exc
            continue
        if resp.status_code == 200:
            return _parse_usda(resp.json(), ingredient)
        if resp.status_code in {429, 502, 503, 504}:
            continue
        return None
    if last_error is not None:
        log.warning("nutrition.usda_failed", error=str(last_error), attempts=attempt + 1)
    return None


def _parse_usda(data: Any, ingredient: str) -> NutritionResult | None:
    foods = data.get("foods") if isinstance(data, dict) else None
    if not foods:
        return None
    first = foods[0]
    description = str(first.get("description", ""))
    nutrients_raw = first.get("foodNutrients", [])
    nutrients: dict[str, Decimal] = {}
    for n in nutrients_raw:
        if not isinstance(n, dict):
            continue
        name = str(n.get("nutrientName", "")).lower()
        value = n.get("value")
        if value is None:
            continue
        try:
            nutrients[name] = Decimal(str(value))
        except (ArithmeticError, ValueError):
            continue
    kcal = nutrients.get("energy (atwater general factors)") or nutrients.get("energy")
    fiber = nutrients.get("fiber, total dietary") or nutrients.get("dietary fiber")
    protein = nutrients.get("protein")
    fat = nutrients.get("total lipid (fat)") or nutrients.get("fat")
    carbs = nutrients.get("carbohydrate, by difference") or nutrients.get("carbohydrates")
    return NutritionResult(
        kcal_per_100g=kcal,
        fiber_g_per_100g=fiber,
        protein_g_per_100g=protein,
        fat_g_per_100g=fat,
        carbs_g_per_100g=carbs,
        source="usda",
        match_score=None,
        match_source_name=description or ingredient,
    )


def lookup_nutrition(
    conn: Connection,
    ingredient: str,
    df: pd.DataFrame | None,
    usda_api_key: str,
) -> NutritionResult | None:
    cached = fetch_cache(conn, ingredient)
    if cached:
        return _from_cached(cached)
    result: NutritionResult | None = None
    if df is not None:
        result = _lookup_cofid(df, ingredient)
    if result is None:
        result = _lookup_usda(usda_api_key, ingredient)
    if result:
        upsert_cache(
            conn,
            ingredient_canonical=ingredient,
            kcal_per_100g=result.kcal_per_100g,
            fiber_g_per_100g=result.fiber_g_per_100g,
            protein_g_per_100g=result.protein_g_per_100g,
            fat_g_per_100g=result.fat_g_per_100g,
            carbs_g_per_100g=result.carbs_g_per_100g,
            source=result.source,
            match_score=result.match_score,
            match_source_name=result.match_source_name,
        )
    return result


def _from_cached(cache: CachedNutrition) -> NutritionResult:
    return NutritionResult(
        kcal_per_100g=cache.kcal_per_100g,
        fiber_g_per_100g=cache.fiber_g_per_100g,
        protein_g_per_100g=cache.protein_g_per_100g,
        fat_g_per_100g=cache.fat_g_per_100g,
        carbs_g_per_100g=cache.carbs_g_per_100g,
        source=cache.source,
        match_score=None,
        match_source_name=None,
    )


def enrich_nutrition(
    settings: Settings, *, engine: Engine | None = None, ignore_coverage: bool = False
) -> int:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    cofid = _load_cofid(settings.nutrition.cofid_path)
    usda_key = settings.nutrition.usda_api_key

    with eng.begin() as conn:
        rows = fetch_enrichment_inputs(conn)
        recipes: dict[int, dict[str, Decimal]] = {}
        servings_map: dict[int, Decimal] = {}
        total = 0
        covered = 0
        for recipe_id, canonical, per_serving_grams, servings_count in rows:
            total += 1
            if not canonical or per_serving_grams is None:
                continue
            result = lookup_nutrition(conn, canonical, cofid, usda_key)
            if result is None:
                continue
            covered += 1
            agg = recipes.setdefault(
                recipe_id,
                {
                    "kcal": Decimal(0),
                    "fiber": Decimal(0),
                    "protein": Decimal(0),
                    "fat": Decimal(0),
                    "carbs": Decimal(0),
                },
            )
            servings_map[recipe_id] = servings_count or Decimal(1)
            scale = per_serving_grams / Decimal(100)
            if result.kcal_per_100g is not None:
                agg["kcal"] += result.kcal_per_100g * scale
            if result.fiber_g_per_100g is not None:
                agg["fiber"] += result.fiber_g_per_100g * scale
            if result.protein_g_per_100g is not None:
                agg["protein"] += result.protein_g_per_100g * scale
            if result.fat_g_per_100g is not None:
                agg["fat"] += result.fat_g_per_100g * scale
            if result.carbs_g_per_100g is not None:
                agg["carbs"] += result.carbs_g_per_100g * scale

        for recipe_id, agg in recipes.items():
            servings = servings_map.get(recipe_id, Decimal(1)) or Decimal(1)
            per_kcal = agg["kcal"]
            per_fiber = agg["fiber"]
            upsert_recipe_nutrition(
                conn,
                recipe_id=recipe_id,
                calories_kcal=per_kcal * servings,
                fiber_g=per_fiber * servings,
                per_serving_kcal=per_kcal,
                per_serving_fiber_g=per_fiber,
                protein_g=agg["protein"],
                fat_g=agg["fat"],
                carbs_g=agg["carbs"],
            )

        coverage = (covered / total) if total else 0.0
        record_metric(conn, MetricName.NUTRITION_ITEMS_TOTAL, total, correlation_id=correlation_id)
        record_metric(
            conn, MetricName.NUTRITION_ITEMS_COVERED, covered, correlation_id=correlation_id
        )
        record_metric(
            conn,
            MetricName.NUTRITION_COVERAGE_RATIO,
            coverage,
            correlation_id=correlation_id,
        )

    log.info("nutrition.complete", total=total, covered=covered, coverage=coverage)

    if total > 0 and coverage < settings.nutrition.coverage_min_ratio and not ignore_coverage:
        raise RuntimeError(
            _coverage_failure_message(coverage, settings.nutrition.coverage_min_ratio)
        )

    return len(recipes)


def fetch_cofid(url: str, dest: Path) -> Path:
    if not url:
        raise ValueError("cofid_url is empty; cannot fetch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest
