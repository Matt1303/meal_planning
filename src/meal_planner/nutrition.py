from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from rapidfuzz import fuzz, process
from sqlalchemy import Connection, Engine

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.nutrition_repo import (
    CachedNutrition,
    delete_cache,
    fetch_cache,
    fetch_declared_nutrition,
    fetch_enrichment_inputs,
    fetch_recipe_serving_grams,
    fetch_sample_raw_text,
    fetch_sub_recipe_lines,
    upsert_cache,
    upsert_recipe_nutrition,
)
from meal_planner.llm import get_llm_client
from meal_planner.llm.base import (
    LLMClient,
    NullLLM,
    NutritionMacros,
    NutritionMatchCandidate,
    NutritionQuery,
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
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return pd.read_csv(path)
    xl = pd.ExcelFile(path)
    candidates: list[tuple[int, pd.DataFrame]] = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        if df.empty:
            continue
        name_col, nutrient_cols = _guess_columns(df)
        if name_col and nutrient_cols:
            candidates.append((len(nutrient_cols), df))
    if not candidates:
        return pd.read_excel(path)
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def _guess_columns(df: pd.DataFrame) -> tuple[str | None, dict[str, str]]:
    cols = {str(c).lower(): str(c) for c in df.columns}
    name_col: str | None = None
    nutrient_cols: dict[str, str] = {}
    fiber_aoac: str | None = None
    fiber_nsp: str | None = None
    for key, original in cols.items():
        if name_col is None and ("food name" in key or key in {"foodname", "name"}):
            name_col = original
        if "kcal" in key and "kcal" not in nutrient_cols:
            nutrient_cols["kcal"] = original
        if "aoac" in key and "fibre" in key:
            fiber_aoac = original
        elif key.startswith("nsp") or " nsp " in f" {key} ":
            fiber_nsp = original
        elif (
            ("fibre" in key or "fiber" in key)
            and "fiber" not in nutrient_cols
            and "aoac" not in key
        ):
            nutrient_cols["fiber"] = original
        if "protein" in key and "protein" not in nutrient_cols:
            nutrient_cols["protein"] = original
        if "fat (g)" in key and "fat" not in nutrient_cols:
            nutrient_cols["fat"] = original
        if "carbohydrate" in key and "carbs" not in nutrient_cols:
            nutrient_cols["carbs"] = original
    if "fiber" not in nutrient_cols:
        if fiber_aoac:
            nutrient_cols["fiber"] = fiber_aoac
        elif fiber_nsp:
            nutrient_cols["fiber"] = fiber_nsp
    return name_col, nutrient_cols


_COFID_MISSING = {"n", "tr", "-", ""}


def _row_value(row: pd.Series, col: str | None) -> Decimal | None:
    if not col:
        return None
    value = row.get(col)
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in _COFID_MISSING:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        return None


def _base_name(name: str) -> str:
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if not parts:
        return name
    head = parts[0]
    if len(parts) >= 2:
        second = parts[1]
        if second and not any(
            second.startswith(prefix)
            for prefix in ("raw", "dried", "cooked", "boiled", "fresh", "frozen")
        ):
            head = f"{head} {second}"
    return head


_PREFERRED_QUALIFIERS = ("raw", "dried", "fresh", "uncooked")
_DEMOTED_TERMS = (
    "with",
    "stuffed",
    "bhaji",
    "curry",
    "pie",
    "pilau",
    "salad",
    "casserole",
    "soup",
    "stew",
    "fried",
    "roasted",
    "battered",
    "in cream",
    "in batter",
    "in sauce",
    "and ",
    ", and",
    "homemade",
)


_BAD_FOOD_NAMES: frozenset[str] = frozenset({"nan", "", "none", "n/a", "na"})


def _lookup_cofid(
    df: pd.DataFrame, ingredient: str, *, min_score: float = 70.0
) -> NutritionResult | None:
    name_col, nutrient_cols = _guess_columns(df)
    if not name_col:
        return None
    names = df[name_col].astype(str).str.lower().tolist()
    base_names = [_base_name(name) for name in names]
    cleaned = _clean_for_fuzzy(ingredient)
    if not cleaned:
        return None

    raw_candidates: list[tuple[str, float, int]] = process.extract(
        cleaned, base_names, limit=20, score_cutoff=70
    )
    if not raw_candidates:
        return None

    query_tokens = [t for t in cleaned.split() if len(t) > 2]
    head_token = query_tokens[0] if query_tokens else ""

    best_idx: int | None = None
    best_score = -1.0
    for _, score, idx in raw_candidates:
        idx_int = int(idx)
        full = names[idx_int]
        if full.strip() in _BAD_FOOD_NAMES or base_names[idx_int].strip() in _BAD_FOOD_NAMES:
            continue
        adj = float(score)
        if any(term in full for term in _DEMOTED_TERMS):
            adj -= 25
        if any(qual in full for qual in _PREFERRED_QUALIFIERS):
            adj += 5
        if head_token:
            full_tokens = re.split(r"[\s,()/-]+", full)
            singular = head_token[:-1] if head_token.endswith("s") else head_token
            plural = head_token if head_token.endswith("s") else head_token + "s"
            if not any(t in (head_token, singular, plural) for t in full_tokens):
                adj -= 30
        adj -= 0.01 * len(full)
        if adj > best_score:
            best_score = adj
            best_idx = idx_int

    if best_idx is None or best_score < min_score:
        return None
    row = df.iloc[best_idx]
    return NutritionResult(
        kcal_per_100g=_row_value(row, nutrient_cols.get("kcal")),
        fiber_g_per_100g=_row_value(row, nutrient_cols.get("fiber")),
        protein_g_per_100g=_row_value(row, nutrient_cols.get("protein")),
        fat_g_per_100g=_row_value(row, nutrient_cols.get("fat")),
        carbs_g_per_100g=_row_value(row, nutrient_cols.get("carbs")),
        source="cofid",
        match_score=Decimal(str(best_score)),
        match_source_name=str(names[best_idx]),
    )


_OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"


_OFF_COMPOSITE_TERMS = (
    "bar",
    "bars",
    "snack",
    "cookie",
    "cookies",
    "biscuit",
    "biscuits",
    "cake",
    "cakes",
    "crisps",
    "chips",
    "popcorn",
    "crackers",
    "wafers",
    "drink",
    "shake",
    "smoothie",
    "salad",
    "soup",
    "stew",
    "curry",
    "sauce",
    "dressing",
    "pie",
    "pudding",
    "mousse",
    "ice cream",
    "sorbet",
    "hummus",
    "houmous",
    "dip",
    "pesto",
    "spread",
    "jam",
    "preserve",
    "mix",
    "blend",
    "flavored",
    "flavoured",
    "with banana",
    "with chocolate",
    "with strawberry",
    "kebab",
    "kofta",
    "koftas",
    "balls",
    "nuggets",
    "fingers",
    "mince",
    "burger",
    "burgers",
    "sticks",
    "fritters",
    "pancakes",
    "waffles",
    "muffin",
    "muffins",
    "loaf",
    "loaves",
    "pizza",
    "pasta",
    "noodles",
    "wrap",
    "wraps",
    "sandwich",
    "topping",
    "filling",
    "frosting",
    "icing",
    "bites",
    "patties",
    "stir fry",
    "ready meal",
    "meal kit",
    "bolognese",
    "energy pouch",
    "pouch",
    "rosti",
    "rissole",
    "fritter",
    "souffle",
)


def _off_pick(products: list[dict[str, Any]], ingredient: str) -> dict[str, Any] | None:
    cleaned = _clean_for_fuzzy(ingredient)
    if not products or not cleaned:
        return None
    query_tokens = [t for t in cleaned.split() if len(t) > 2]
    head_token = query_tokens[0] if query_tokens else ""
    query_token_count = max(len(query_tokens), 1)

    best: dict[str, Any] | None = None
    best_score = -1.0
    for product in products:
        if not isinstance(product, dict):
            continue
        nutriments = product.get("nutriments")
        if not isinstance(nutriments, dict):
            continue
        if nutriments.get("energy-kcal_100g") is None and nutriments.get("energy_100g") is None:
            continue
        if nutriments.get("proteins_100g") is None:
            continue
        name_raw = str(product.get("product_name") or product.get("generic_name") or "").lower()
        if not name_raw:
            continue
        name_clean = _clean_for_fuzzy(name_raw)
        if not name_clean:
            continue
        product_tokens = [t for t in name_clean.split() if t]
        product_token_count = max(len(product_tokens), 1)
        # Hard cap: a single-token ingredient (e.g. "broccoli") must not match
        # a product name with > 3 tokens, otherwise OFF picks branded composites.
        if query_token_count == 1 and product_token_count > 3:
            continue
        if query_token_count >= 2 and product_token_count > query_token_count + 4:
            continue
        # For single-token queries, the query word must anchor the product
        # name: either be the first significant token, or be the only
        # ingredient-like token after dropping qualifiers (organic, fresh,
        # raw, ...). This rejects "Cocoa & Banana" for "banana" while
        # accepting "Organic Almond Milk" for "almond milk".
        if query_token_count == 1 and head_token:
            qualifier_tokens = {
                "organic",
                "fresh",
                "raw",
                "whole",
                "frozen",
                "dried",
                "powder",
                "natural",
                "unsweetened",
                "shelled",
                "ground",
            }
            significant = [t for t in product_tokens if t not in qualifier_tokens]
            singular = head_token[:-1] if head_token.endswith("s") else head_token
            plural = head_token if head_token.endswith("s") else head_token + "s"
            if not significant or significant[0] not in (head_token, singular, plural):
                continue
        score = float(fuzz.WRatio(cleaned, name_clean))
        score -= 6 * max(0, product_token_count - query_token_count - 1)
        if " & " in name_raw or " and " in f" {name_raw} ":
            score -= 35
        if head_token:
            singular = head_token[:-1] if head_token.endswith("s") else head_token
            plural = head_token if head_token.endswith("s") else head_token + "s"
            if not any(t in (head_token, singular, plural) for t in product_tokens):
                score -= 40
        if any(term in name_clean for term in _OFF_COMPOSITE_TERMS):
            score -= 35
        if any(term in name_clean for term in _DEMOTED_TERMS):
            score -= 20
        completeness = product.get("completeness")
        if completeness is not None:
            try:
                score += float(completeness) * 5
            except (TypeError, ValueError):
                pass
        if nutriments.get("fiber_100g") is not None:
            score += 2
        if nutriments.get("carbohydrates_100g") is not None:
            score += 1
        if score > best_score:
            best_score = score
            best = product
    if best is None or best_score < 75:
        return None
    return best


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _lookup_open_food_facts(
    ingredient: str,
    *,
    user_agent: str,
    timeout: int,
    enabled: bool,
    countries: str = "",
    lc: str = "en",
) -> NutritionResult | None:
    if not enabled:
        return None
    cleaned = _clean_for_fuzzy(ingredient)
    if not cleaned:
        return None
    params: dict[str, str | int] = {
        "search_terms": cleaned,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 20,
        "fields": "product_name,generic_name,nutriments,completeness,brands",
        "sort_by": "popularity_key",
        "lc": lc,
    }
    if countries:
        params["countries"] = countries
    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            resp = requests.get(_OFF_SEARCH_URL, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            continue
        if resp.status_code == 200:
            try:
                payload = resp.json()
            except ValueError:
                return None
            products_raw = payload.get("products") if isinstance(payload, dict) else None
            products = [p for p in products_raw if isinstance(p, dict)] if products_raw else []
            picked = _off_pick(products, ingredient)
            if picked is None:
                return None
            nutriments = picked.get("nutriments") or {}
            kcal = nutriments.get("energy-kcal_100g")
            if kcal is None and nutriments.get("energy_100g") is not None:
                try:
                    kcal = float(nutriments["energy_100g"]) / 4.184
                except (TypeError, ValueError):
                    kcal = None
            return NutritionResult(
                kcal_per_100g=_decimal_or_none(kcal),
                fiber_g_per_100g=_decimal_or_none(nutriments.get("fiber_100g")),
                protein_g_per_100g=_decimal_or_none(nutriments.get("proteins_100g")),
                fat_g_per_100g=_decimal_or_none(nutriments.get("fat_100g")),
                carbs_g_per_100g=_decimal_or_none(nutriments.get("carbohydrates_100g")),
                source="open_food_facts",
                match_score=None,
                match_source_name=str(picked.get("product_name") or ""),
            )
        if resp.status_code in {429, 502, 503, 504}:
            continue
        return None
    if last_error is not None:
        log.warning("nutrition.off_failed", error=str(last_error))
    return None


def _lookup_usda(api_key: str, ingredient: str) -> NutritionResult | None:
    if not api_key:
        return None
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params: dict[str, Any] = {
        "api_key": api_key,
        "query": ingredient,
        "pageSize": 10,
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


def _pick_usda_food(foods: list[dict[str, Any]], ingredient: str) -> dict[str, Any] | None:
    cleaned = _clean_for_fuzzy(ingredient)
    if not foods:
        return None
    descriptions = [_clean_for_fuzzy(str(f.get("description", ""))) for f in foods]
    raw_candidates: list[tuple[str, float, int]] = process.extract(
        cleaned, descriptions, limit=len(descriptions), score_cutoff=50
    )
    if not raw_candidates:
        return foods[0]
    best_idx: int | None = None
    best_score = -1.0
    for _, score, idx in raw_candidates:
        idx_int = int(idx)
        description = descriptions[idx_int]
        adj = float(score)
        if any(term in description for term in _DEMOTED_TERMS):
            adj -= 25
        if any(qual in description for qual in _PREFERRED_QUALIFIERS):
            adj += 5
        adj -= 0.1 * len(description)
        if adj > best_score:
            best_score = adj
            best_idx = idx_int
    if best_idx is None:
        return foods[0]
    return foods[best_idx]


def _parse_usda(data: Any, ingredient: str) -> NutritionResult | None:
    foods = data.get("foods") if isinstance(data, dict) else None
    if not foods:
        return None
    first = _pick_usda_food(foods, ingredient) or foods[0]
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
    *,
    off_enabled: bool = True,
    off_user_agent: str = "meal-planner/0.2",
    off_timeout: int = 10,
    off_countries: str = "",
    off_lc: str = "en",
) -> NutritionResult | None:
    cached = fetch_cache(conn, ingredient)
    if cached:
        return _from_cached(cached)
    result: NutritionResult | None = _lookup_open_food_facts(
        ingredient,
        user_agent=off_user_agent,
        timeout=off_timeout,
        enabled=off_enabled,
        countries=off_countries,
        lc=off_lc,
    )
    if result is None and df is not None:
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
        match_score=cache.match_score,
        match_source_name=cache.match_source_name,
    )


_CONFIDENCE_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


def _confidence_meets(value: str, minimum: str) -> bool:
    return _CONFIDENCE_RANK.get(value, 0) >= _CONFIDENCE_RANK.get(minimum, 2)


def _macros_to_result(macros: NutritionMacros) -> NutritionResult:
    return NutritionResult(
        kcal_per_100g=_decimal_or_none(macros.kcal_per_100g),
        fiber_g_per_100g=_decimal_or_none(macros.fiber_g_per_100g),
        protein_g_per_100g=_decimal_or_none(macros.protein_g_per_100g),
        fat_g_per_100g=_decimal_or_none(macros.fat_g_per_100g),
        carbs_g_per_100g=_decimal_or_none(macros.carbs_g_per_100g),
        source=f"claude_{macros.confidence}",
        match_score=Decimal("100") if macros.confidence == "high" else Decimal("85"),
        match_source_name=macros.notes or macros.ingredient_canonical,
    )


def _resolve_claude_macros_committed(
    eng: Engine,
    llm: LLMClient,
    pending: list[str],
    sample_text: dict[str, str],
    batch_size: int,
    min_conf: str,
) -> set[str]:
    """Fetch Claude per-100g macros for pending canonicals and commit each
    batch's accepted results to the cache immediately, so an interrupt keeps
    the work (and API spend) done so far. Returns the canonicals cached."""
    cached: set[str] = set()
    total_returned = 0
    total_accepted = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        queries = [
            NutritionQuery(
                ingredient_canonical=canonical,
                sample_raw_text=sample_text.get(canonical, canonical),
            )
            for canonical in batch
        ]
        try:
            macros = llm.fetch_nutrition_macros(queries)
        except Exception as exc:
            log.warning("nutrition.claude_failed", error=str(exc), batch=start)
            continue
        total_returned += len(macros)
        with eng.begin() as conn:
            for item in macros:
                if not _confidence_meets(item.confidence, min_conf):
                    continue
                if item.kcal_per_100g is None:
                    continue
                result = _macros_to_result(item)
                upsert_cache(
                    conn,
                    ingredient_canonical=item.ingredient_canonical,
                    kcal_per_100g=result.kcal_per_100g,
                    fiber_g_per_100g=result.fiber_g_per_100g,
                    protein_g_per_100g=result.protein_g_per_100g,
                    fat_g_per_100g=result.fat_g_per_100g,
                    carbs_g_per_100g=result.carbs_g_per_100g,
                    source=result.source,
                    match_score=result.match_score,
                    match_source_name=result.match_source_name,
                )
                cached.add(item.ingredient_canonical)
                total_accepted += 1
    log.info(
        "nutrition.claude_primary_complete",
        queried=len(pending),
        returned=total_returned,
        accepted=total_accepted,
    )
    return cached


def _verify_and_correct_committed(
    eng: Engine,
    settings: Settings,
    canonicals: list[str],
    cofid: pd.DataFrame | None,
) -> None:
    """LLM-verify low-confidence cached matches, committing each batch's
    corrections to the cache immediately so an interrupt keeps progress.

    Reads suspects from the committed cache, so it can run as its own phase
    after the lookup phase has persisted everything."""
    if not settings.nutrition.llm_verify_enabled:
        return
    llm = get_llm_client(settings.llm)
    if isinstance(llm, NullLLM):
        return

    threshold = settings.nutrition.llm_verify_score_threshold
    with eng.connect() as conn:
        sample_text = fetch_sample_raw_text(conn)
        suspects: list[NutritionMatchCandidate] = []
        for canonical in canonicals:
            cached = fetch_cache(conn, canonical)
            if cached is None:
                continue
            # Treat a missing score (most OFF hits) as suspect — the OFF picker
            # is heuristic, so we still want the LLM to sanity-check it.
            score = float(cached.match_score) if cached.match_score is not None else 0.0
            if score >= threshold:
                continue
            suspects.append(
                NutritionMatchCandidate(
                    ingredient_canonical=canonical,
                    ingredient_raw_text=sample_text.get(canonical, canonical),
                    matched_food_name=cached.match_source_name,
                    match_source=cached.source or "unknown",
                    match_score=score,
                    kcal_per_100g=float(cached.kcal_per_100g) if cached.kcal_per_100g else None,
                    protein_per_100g=(
                        float(cached.protein_g_per_100g) if cached.protein_g_per_100g else None
                    ),
                    fiber_per_100g=(
                        float(cached.fiber_g_per_100g) if cached.fiber_g_per_100g else None
                    ),
                )
            )
    if not suspects:
        return

    log.info("nutrition.llm_verify_start", suspect_count=len(suspects))
    batch_size = settings.nutrition.llm_verify_batch_size
    rejected = 0
    re_looked_up = 0
    for start in range(0, len(suspects), batch_size):
        batch = suspects[start : start + batch_size]
        try:
            verdicts = llm.verify_nutrition_matches(batch)
        except Exception as exc:
            log.warning("nutrition.llm_verify_failed", error=str(exc), batch=start)
            continue
        alternatives = {
            v.ingredient_canonical: v.alternative_query
            for v in verdicts
            if v.decision == "alternative" and v.alternative_query
        }
        # Resolve any alternative queries (network) before opening the tx.
        replacements: dict[str, NutritionResult] = {}
        for canonical, alt_query in alternatives.items():
            new_result = _lookup_with_fallback(
                alt_query, cofid, settings, prefer_canonical=canonical, cofid_min_score=90.0
            )
            if new_result is not None:
                replacements[canonical] = new_result
        with eng.begin() as conn:
            for verdict in verdicts:
                canonical = verdict.ingredient_canonical
                if verdict.decision == "reject":
                    delete_cache(conn, canonical)
                    rejected += 1
                elif verdict.decision == "alternative":
                    new_result = replacements.get(canonical)
                    if new_result is not None:
                        delete_cache(conn, canonical)
                        upsert_cache(
                            conn,
                            ingredient_canonical=canonical,
                            kcal_per_100g=new_result.kcal_per_100g,
                            fiber_g_per_100g=new_result.fiber_g_per_100g,
                            protein_g_per_100g=new_result.protein_g_per_100g,
                            fat_g_per_100g=new_result.fat_g_per_100g,
                            carbs_g_per_100g=new_result.carbs_g_per_100g,
                            source=f"{new_result.source}_llm",
                            match_score=Decimal("100"),
                            match_source_name=new_result.match_source_name,
                        )
                        re_looked_up += 1
                    else:
                        # LLM said the match is wrong and no clean replacement
                        # was found — drop the bad match.
                        delete_cache(conn, canonical)
                        rejected += 1
    log.info(
        "nutrition.llm_verify_complete",
        suspect_count=len(suspects),
        rejected=rejected,
        re_looked_up=re_looked_up,
    )


def _lookup_with_fallback(
    query: str,
    cofid: pd.DataFrame | None,
    settings: Settings,
    *,
    prefer_canonical: str = "",
    cofid_min_score: float = 70.0,
) -> NutritionResult | None:
    result = _lookup_open_food_facts(
        query,
        user_agent=settings.nutrition.open_food_facts_user_agent,
        timeout=settings.nutrition.open_food_facts_timeout,
        enabled=settings.nutrition.open_food_facts_enabled,
        countries=settings.nutrition.open_food_facts_countries,
        lc=settings.nutrition.open_food_facts_lc,
    )
    if result is None and cofid is not None:
        result = _lookup_cofid(cofid, query, min_score=cofid_min_score)
    if result is None:
        result = _lookup_usda(settings.nutrition.usda_api_key, query)
    return result


def enrich_nutrition(
    settings: Settings, *, engine: Engine | None = None, ignore_coverage: bool = False
) -> int:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    cofid = _load_cofid(settings.nutrition.cofid_path)
    usda_key = settings.nutrition.usda_api_key
    off_enabled = settings.nutrition.open_food_facts_enabled
    off_user_agent = settings.nutrition.open_food_facts_user_agent
    off_timeout = settings.nutrition.open_food_facts_timeout
    off_countries = settings.nutrition.open_food_facts_countries
    off_lc = settings.nutrition.open_food_facts_lc
    cooking_oils = {oil.strip().lower() for oil in settings.nutrition.cooking_oils}
    oil_absorption = Decimal(str(settings.nutrition.cooking_oil_absorption))

    # Phase 0: read inputs (no write, short-lived connection).
    with eng.connect() as conn:
        rows = fetch_enrichment_inputs(conn)
        unique_canonicals = sorted({str(r[1]) for r in rows if r[1]})
        sample_text = fetch_sample_raw_text(conn)
        cached_canonicals = {c for c in unique_canonicals if fetch_cache(conn, c) is not None}

    # Phase 1: Claude primary — commits per batch so an interrupt keeps work.
    pending = [c for c in unique_canonicals if c not in cached_canonicals]
    if settings.nutrition.llm_macros_primary and pending:
        llm_client = get_llm_client(settings.llm)
        if not isinstance(llm_client, NullLLM):
            log.info("nutrition.claude_primary_start", to_query=len(pending))
            newly_cached = _resolve_claude_macros_committed(
                eng,
                llm_client,
                pending,
                sample_text,
                settings.nutrition.llm_macros_batch_size,
                settings.nutrition.llm_macros_min_confidence,
            )
            pending = [c for c in pending if c not in newly_cached]

    # Phase 2: OFF/CoFID/USDA fallback for anything still uncached — one
    # committed transaction per ingredient so partial progress survives.
    for canonical in pending:
        with eng.begin() as conn:
            lookup_nutrition(
                conn,
                canonical,
                cofid,
                usda_key,
                off_enabled=off_enabled,
                off_user_agent=off_user_agent,
                off_timeout=off_timeout,
                off_countries=off_countries,
                off_lc=off_lc,
            )

    # Phase 3: LLM verify — reads suspects from the committed cache and
    # commits each batch's corrections immediately.
    _verify_and_correct_committed(eng, settings, unique_canonicals, cofid)

    # Phase 4: aggregate per-recipe nutrition from the now-committed cache.
    with eng.begin() as conn:
        nutrition_by_canonical: dict[str, NutritionResult] = {}
        for canonical in unique_canonicals:
            cached = fetch_cache(conn, canonical)
            if cached is not None:
                nutrition_by_canonical[canonical] = _from_cached(cached)

        recipes: dict[int, dict[str, Decimal]] = {}
        servings_map: dict[int, Decimal] = {}
        total = 0
        covered = 0
        for recipe_id, canonical, per_serving_grams, servings_count in rows:
            total += 1
            if not canonical or per_serving_grams is None:
                continue
            result = nutrition_by_canonical.get(canonical)
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
            effective_grams = per_serving_grams
            if canonical.strip().lower() in cooking_oils:
                effective_grams = per_serving_grams * oil_absorption
            scale = effective_grams / Decimal(100)
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

        # Expand "(separate recipe)" rows: add the sub-recipe's per-gram
        # contribution scaled by the parent line's per_serving_grams.
        sub_rows = fetch_sub_recipe_lines(conn)
        sub_recipe_grams = fetch_recipe_serving_grams(conn)
        sub_expanded = 0
        for parent_id, sub_id, parent_grams in sub_rows:
            if parent_grams is None:
                continue
            sub_total_grams = sub_recipe_grams.get(sub_id)
            sub_agg = recipes.get(sub_id)
            if not sub_total_grams or not sub_agg or sub_total_grams <= 0:
                continue
            scale = Decimal(parent_grams) / sub_total_grams
            parent_agg = recipes.setdefault(
                parent_id,
                {
                    "kcal": Decimal(0),
                    "fiber": Decimal(0),
                    "protein": Decimal(0),
                    "fat": Decimal(0),
                    "carbs": Decimal(0),
                },
            )
            for key in ("kcal", "fiber", "protein", "fat", "carbs"):
                parent_agg[key] += sub_agg[key] * scale
            sub_expanded += 1
        log.info("nutrition.sub_recipes_expanded", expanded=sub_expanded)

        # Recipes with a Paprika Nutrition section use those declared per-serving
        # values verbatim, overriding the ingredient-computed aggregation — the
        # meal is consumed as the recipe states (e.g. a fixed breakfast smoothie).
        declared = fetch_declared_nutrition(conn)
        declared_used = 0
        for recipe_id in set(recipes) | set(declared):
            agg = recipes.get(
                recipe_id,
                {
                    "kcal": Decimal(0),
                    "fiber": Decimal(0),
                    "protein": Decimal(0),
                    "fat": Decimal(0),
                    "carbs": Decimal(0),
                },
            )
            servings = servings_map.get(recipe_id, Decimal(1)) or Decimal(1)
            per_kcal = agg["kcal"]
            per_fiber = agg["fiber"]
            per_protein = agg["protein"]
            per_fat = agg["fat"]
            per_carbs = agg["carbs"]
            if recipe_id in declared:
                d_kcal, d_protein, d_fiber, d_fat, d_carbs, d_servings = declared[recipe_id]
                servings = d_servings or Decimal(1)
                if d_kcal is not None:
                    per_kcal = d_kcal
                if d_protein is not None:
                    per_protein = d_protein
                if d_fiber is not None:
                    per_fiber = d_fiber
                if d_fat is not None:
                    per_fat = d_fat
                if d_carbs is not None:
                    per_carbs = d_carbs
                declared_used += 1
            upsert_recipe_nutrition(
                conn,
                recipe_id=recipe_id,
                calories_kcal=per_kcal * servings,
                fiber_g=per_fiber * servings,
                per_serving_kcal=per_kcal,
                per_serving_fiber_g=per_fiber,
                protein_g=per_protein * servings,
                fat_g=per_fat * servings,
                carbs_g=per_carbs * servings,
                per_serving_protein_g=per_protein,
                per_serving_fat_g=per_fat,
                per_serving_carbs_g=per_carbs,
            )
        if declared_used:
            log.info("nutrition.declared_overrides", recipes=declared_used)

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
