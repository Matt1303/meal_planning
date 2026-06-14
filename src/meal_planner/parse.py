from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from rapidfuzz import process
from sqlalchemy import Connection, Engine, text

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.parse_repo import (
    CachedParse,
    ParseUpdate,
    fetch_cache,
    fetch_overrides,
    fetch_unparsed_rows,
    write_parse_update,
)
from meal_planner.food_list import load_food_groups, load_synonyms
from meal_planner.llm.base import LLMClient, NullLLM
from meal_planner.llm.factory import get_llm_client
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName
from meal_planner.units import UnitTable

log = get_logger(__name__)

UNITS_REGEX = (
    r"kg|g|mg|ml|l|tsp|tbsp|teaspoon|teaspoons|tablespoon|tablespoons|"
    r"cup|cups|oz|ounce|ounces|lb|pound|pounds|stick|sticks|clove|cloves|"
    r"sprig|sprigs|handful|pinch|dash"
)

_RANGE_PATTERN = re.compile(
    rf"(?P<q1>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:-|to|–)\s*"
    rf"(?P<q2>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>{UNITS_REGEX})\b",
    re.IGNORECASE,
)
_MULT_PATTERN = re.compile(
    rf"(?P<count>\d+)\s*x\s*(?P<q>\d+(?:\.\d+)?)\s*(?P<unit>{UNITS_REGEX})\b",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    rf"(?P<q>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>{UNITS_REGEX})\b",
    re.IGNORECASE,
)
_QUANTITY_TOKEN = re.compile(r"^\d+([./]\d+)*$")


@dataclass(frozen=True)
class ParseAttempt:
    quantity_value: Decimal | None
    quantity_unit: str | None
    quantity_grams: Decimal | None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


_UNIT_TOKENS: frozenset[str] = frozenset(
    {
        "g",
        "gram",
        "grams",
        "kg",
        "kilogram",
        "kilograms",
        "mg",
        "milligram",
        "milligrams",
        "ml",
        "millilitre",
        "millilitres",
        "l",
        "liter",
        "liters",
        "litre",
        "litres",
        "tsp",
        "teaspoon",
        "teaspoons",
        "tbsp",
        "tablespoon",
        "tablespoons",
        "cup",
        "cups",
        "oz",
        "ounce",
        "ounces",
        "lb",
        "pound",
        "pounds",
        "stick",
        "sticks",
        "clove",
        "cloves",
        "sprig",
        "sprigs",
        "handful",
        "pinch",
        "dash",
        # container / packaging words that aren't part of the ingredient name
        "can",
        "cans",
        "tin",
        "tins",
        "tinned",
        "jar",
        "jars",
        "packet",
        "packets",
        "pack",
        "packs",
        "tub",
        "tubs",
        "carton",
        "cartons",
        "bottle",
        "bottles",
        "box",
        "boxes",
        "bag",
        "bags",
    }
)


_NUMBER_UNIT_PREFIX = re.compile(
    rf"^\d+(?:[./]\d+)?(?:[-–]\d+(?:[./]\d+)?)?({UNITS_REGEX})$", re.IGNORECASE
)


def strip_quantity(value: str) -> str:
    tokens = re.split(r"\s+", value)
    kept: list[str] = []
    for tok in tokens:
        clean = tok.strip().lower()
        if not clean:
            continue
        if _QUANTITY_TOKEN.match(clean):
            continue
        if clean in _UNIT_TOKENS:
            continue
        if _NUMBER_UNIT_PREFIX.match(clean):
            continue
        kept.append(tok)
    return " ".join(kept).strip()


def _parse_fraction(text: str) -> Decimal | None:
    parts = text.split("/")
    if len(parts) != 2:
        return None
    try:
        a, b = Decimal(parts[0]), Decimal(parts[1])
        if b == 0:
            return None
        return a / b
    except (InvalidOperation, ValueError):
        return None


def _parse_number(text: str) -> Decimal | None:
    s = text.strip()
    if " " in s and "/" in s:
        head, tail = s.split(" ", 1)
        frac = _parse_fraction(tail)
        if frac is None:
            return None
        try:
            return Decimal(head) + frac
        except (InvalidOperation, ValueError):
            return None
    if "/" in s:
        return _parse_fraction(s)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def regex_parse_quantity(text: str) -> tuple[Decimal | None, str | None]:
    cleaned = text.replace("–", "-")
    m = _RANGE_PATTERN.search(cleaned)
    if m:
        a = _parse_number(m.group("q1"))
        b = _parse_number(m.group("q2"))
        unit = m.group("unit").lower()
        if a is not None and b is not None:
            return (a + b) / Decimal(2), unit
    m = _MULT_PATTERN.search(cleaned)
    if m:
        count = _parse_number(m.group("count"))
        q = _parse_number(m.group("q"))
        unit = m.group("unit").lower()
        if count is not None and q is not None:
            return count * q, unit
    m = _SINGLE_PATTERN.search(cleaned)
    if m:
        q = _parse_number(m.group("q"))
        unit = m.group("unit").lower()
        if q is not None:
            return q, unit
    return None, None


_FRACTION_PREFIX = re.compile(
    r"\b(?P<frac>quarter|half|three[ -]quarters?|two[ -]thirds?|one[ -]third|third)\s+(?:an?\s+|of\s+(?:an?\s+)?)?",
    re.IGNORECASE,
)
_FRACTION_VALUES: dict[str, str] = {
    "quarter": "0.25",
    "third": "0.333",
    "one third": "0.333",
    "two thirds": "0.667",
    "two-thirds": "0.667",
    "half": "0.5",
    "three quarters": "0.75",
    "three-quarters": "0.75",
}

_QUANTULUM_NULL_UNITS = {"year", "years", "dimensionless"}


_SIZE_ADJECTIVE = re.compile(
    r"(?P<num>\d|½|¼|¾|⅓|⅔)\s+(?:large|medium|small|big|extra[- ]large|jumbo|mini)\s+",
    re.IGNORECASE,
)

# A leading "One / A / An" before a number is a redundant container count
# ("One 400 gram can black beans" = a 400 g can) that confuses quantulum.
_REDUNDANT_LEADING_ONE = re.compile(r"^\s*(?:one|a|an)\s+(?=\d)", re.IGNORECASE)

# Container/packaging words sitting between a quantity and the food
# ("400 gram can black beans") break quantulum's unit parsing; strip them
# before extracting the quantity.
_CONTAINER_WORDS = re.compile(
    r"\b(?:cans?|tins?|tinned|jars?|packets?|packs?|tubs?|cartons?|bottles?|boxe?s?|bags?)\b",
    re.IGNORECASE,
)


def _strip_containers(text: str) -> str:
    return _CONTAINER_WORDS.sub(" ", text)


# A spaced slash separates two alternative ingredients ("coconut milk /
# coconut cream"); an unspaced slash is a dual-unit quantity ("1 cup/250ml")
# or an alias ("chilli/hot pepper"), which must be left intact.
_ALT_SLASH = re.compile(r"\s+/\s+")


def _primary_clause(raw: str) -> str:
    """For an "A / B" alternative-ingredient line, parse only the first (A)."""
    return _ALT_SLASH.split(raw, 1)[0].strip() or raw


def _preprocess_fraction_words(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        frac = match.group("frac").lower().replace("-", " ")
        value = _FRACTION_VALUES.get(frac)
        return f"{value} " if value else match.group(0)

    out = _FRACTION_PREFIX.sub(_replace, text)
    # Drop a redundant leading container count ("One 400 gram can ..." ->
    # "400 gram can ...") so quantulum reads the real quantity.
    out = _REDUNDANT_LEADING_ONE.sub("", out)
    # Drop a size adjective sitting between a count and the food ("1 large
    # onion" -> "1 onion") so quantulum reads the count and the piece fallback
    # can size it.
    out = _SIZE_ADJECTIVE.sub(lambda m: f"{m.group('num')} ", out)
    return out


def quantulum_parse(text: str) -> tuple[Decimal | None, str | None, bool]:
    try:
        from quantulum3 import parser as qty_parser
    except Exception:
        return None, None, True
    preprocessed = _preprocess_fraction_words(text)
    try:
        results = qty_parser.parse(preprocessed)
    except Exception:
        return None, None, False
    if not results:
        return None, None, True
    first = results[0]
    value: Decimal | None = None
    raw_value = getattr(first, "value", None)
    if raw_value is not None:
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError):
            value = None
    unit: str | None = None
    unit_obj = getattr(first, "unit", None)
    if unit_obj is not None:
        unit_name = getattr(unit_obj, "name", None)
        if isinstance(unit_name, str):
            unit = unit_name
    if unit is not None and unit.strip().lower() in _QUANTULUM_NULL_UNITS:
        unit = None
    return value, unit, True


@dataclass(frozen=True)
class ParseContext:
    food_groups: dict[str, str]
    synonyms: dict[str, str]
    overrides: dict[str, tuple[str | None, str | None]]
    units: UnitTable
    portion_sizes: dict[str, float]
    fuzzy_min_score: float
    llm_threshold: float
    group_names: list[str]


def build_context(
    settings: Settings, overrides: dict[str, tuple[str | None, str | None]]
) -> ParseContext:
    food_groups = load_food_groups(_resolve_food_paths(settings))
    synonyms = load_synonyms(settings.parse.synonyms_path)
    units = UnitTable.from_paths(
        settings.parse.unit_grams_path,
        settings.parse.density_path,
        piece_path=settings.parse.piece_grams_path,
    )
    return ParseContext(
        food_groups=food_groups,
        synonyms=synonyms,
        overrides=overrides,
        units=units,
        portion_sizes=dict(settings.portion_sizes),
        fuzzy_min_score=settings.parse.fuzzy_min_score,
        llm_threshold=settings.parse.llm_threshold,
        group_names=sorted(set(food_groups.values())),
    )


def _resolve_food_paths(settings: Settings) -> list[Path]:
    paths = list(settings.parse.food_list_paths)
    if not paths:
        paths = [Path("config/food_list_canonical.txt")]
    return paths


SUB_RECIPE_MARKER = re.compile(
    r"\(\s*(?:separate\s+recipe|see\s+recipe|recipe\s+(?:above|below))(?:\s*[,;].*?)?\s*\)",
    re.IGNORECASE,
)
SUB_RECIPE_FUZZY_MIN_SCORE = 90.0


def detect_sub_recipe_name(raw_text: str) -> str | None:
    """Return the recipe-title candidate when the raw line carries a
    (separate recipe) / (see recipe) / (recipe above/below) marker.

    Strips the marker, any trailing "or X" alternative clause, leading
    quantity, and trailing modifiers like ", to serve" so what is left is
    just the recipe name to fuzzy-match.
    """
    match = SUB_RECIPE_MARKER.search(raw_text)
    if match is None:
        return None
    before = raw_text[: match.start()]
    after = raw_text[match.end() :]
    # Drop a trailing "or X" clause that often follows the marker (e.g.
    # "60 grams Pumpkin Hummus (separate recipe) or other hummus").
    after = re.sub(r"^\s*or\s+[^,.]+", "", after, flags=re.IGNORECASE)
    candidate = (before + " " + after).strip()
    # Strip trailing modifiers like ", to serve" / ", chopped" — they
    # describe how the sub-recipe is used, not part of its title.
    candidate = re.split(r",\s*(?:to\s+\w+|for\s+\w+|chopped|sliced|diced|crushed)", candidate)[0]
    candidate = strip_quantity(candidate) or candidate
    candidate = candidate.strip(" ,.-")
    # Strip wrapper prefixes that describe how the sub-recipe is being used
    # rather than its title ("Leftover X", "reserved X", "1 recipe X").
    candidate = re.sub(
        r"^(?:leftover|reserved|remaining|left[\s-]*over|"
        r"\d+\s*(?:recipe|portion|portions|serving|servings)|"
        r"recipes?(?:\s+of)?|portion(?:s)?\s+of|serving(?:s)?\s+of)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    return candidate or None


def _quantulum_then_regex(raw: str, errors: list[int]) -> tuple[Decimal | None, str | None]:
    qty_value, qty_unit, ok = quantulum_parse(raw)
    if not ok:
        errors[0] += 1
    needs_fallback = (
        qty_value is None
        or qty_unit is None
        or (qty_unit and qty_unit.strip().lower() == "dimensionless")
    )
    if needs_fallback:
        rx_value, rx_unit = regex_parse_quantity(raw)
        if rx_value is not None:
            qty_value = rx_value
        if rx_unit is not None:
            qty_unit = rx_unit
    return qty_value, qty_unit


def _resolve_canonical(
    raw: str, ingredient_name: str, ctx: ParseContext
) -> tuple[str | None, str | None]:
    norm = normalize_text(raw)
    if norm in ctx.overrides:
        canonical, group = ctx.overrides[norm]
        return canonical, group
    syn = ctx.synonyms.get(norm) or ctx.synonyms.get(normalize_text(ingredient_name))
    if syn:
        return syn, ctx.food_groups.get(syn)
    if ctx.food_groups:
        candidates = list(ctx.food_groups.keys())
        result = process.extractOne(normalize_text(ingredient_name), candidates)
        if result:
            match_str = str(result[0])
            score_value = float(result[1] or 0)
            if (score_value / 100.0) >= ctx.fuzzy_min_score:
                return match_str, ctx.food_groups.get(match_str)
    return None, None


def _portions(
    grams: Decimal | None,
    food_group: str | None,
    portion_sizes: dict[str, float],
) -> tuple[Decimal | None, bool | None]:
    if grams is None or food_group is None or food_group not in portion_sizes:
        return None, None
    size = Decimal(str(portion_sizes[food_group]))
    if size <= 0:
        return None, None
    return grams / size, grams >= size


def resolve_sub_recipes(conn: Connection) -> int:
    """Detect (separate recipe) markers in recipe_ingredient.raw_text and
    populate sub_recipe_id by fuzzy-matching the title prefix against
    meal_planning.recipe.title. Returns the number of rows resolved.
    """
    candidate_rows = conn.execute(
        text(
            """
            SELECT recipe_id, raw_text
            FROM meal_planning.recipe_ingredient
            WHERE (raw_text ILIKE '%(separate recipe%'
                OR raw_text ILIKE '%(see recipe%'
                OR raw_text ILIKE '%(recipe above%'
                OR raw_text ILIKE '%(recipe below%')
              AND sub_recipe_id IS NULL
            """
        )
    ).fetchall()
    if not candidate_rows:
        return 0

    title_rows = conn.execute(
        text("SELECT recipe_id, title FROM meal_planning.recipe WHERE title IS NOT NULL")
    ).fetchall()
    titles = [str(r[1]) for r in title_rows]
    title_to_id = {str(r[1]).lower(): int(r[0]) for r in title_rows}

    resolved = 0
    for parent_recipe_id, raw_text in candidate_rows:
        name = detect_sub_recipe_name(str(raw_text))
        if not name:
            continue
        # Exact lower-cased match wins outright; also accept substring match
        # (e.g. "Light Vegetable Broth" referenced from "Biome Broth Unleashed
        # / Light Vegetable Broth" recipe title).
        sub_id = title_to_id.get(name.lower())
        if sub_id is None:
            match = process.extractOne(name, titles, score_cutoff=SUB_RECIPE_FUZZY_MIN_SCORE)
            if match is None:
                continue
            matched_title = str(match[0])
            # Require the candidate's first significant token to appear in the
            # matched title; otherwise stale/wrong fuzzy hits sneak through
            # ("Almond Milk" -> "...Coconut Milk").
            head_tokens = [t for t in name.lower().split() if len(t) > 2]
            head_token = head_tokens[0] if head_tokens else ""
            if head_token and head_token not in matched_title.lower():
                continue
            sub_id = title_to_id.get(matched_title.lower())
            if sub_id is None:
                continue
        if int(sub_id) == int(parent_recipe_id):
            continue  # don't link a recipe to itself
        conn.execute(
            text(
                """
                UPDATE meal_planning.recipe_ingredient
                SET sub_recipe_id = :sub
                WHERE recipe_id = :parent AND raw_text = :raw
                """
            ),
            {"sub": int(sub_id), "parent": int(parent_recipe_id), "raw": str(raw_text)},
        )
        resolved += 1
    return resolved


def parse_ingredients(settings: Settings, *, engine: Engine | None = None) -> int:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    llm: LLMClient = get_llm_client(settings.llm)
    use_llm = not isinstance(llm, NullLLM)

    quantulum_errors = [0]
    total = 0
    cached_count = 0
    llm_used = 0
    llm_invalid = 0

    with eng.begin() as conn:
        overrides = fetch_overrides(conn)
        ctx = build_context(settings, overrides)
        rows = fetch_unparsed_rows(conn)

        pending: list[tuple[ParseUpdate, str | None]] = []
        llm_inputs: list[tuple[str, ParseUpdate]] = []

        for recipe_id, raw_text, servings_count in rows:
            total += 1
            cache = fetch_cache(conn, raw_text)
            base = _from_cache_or_compute(
                cache,
                raw_text=raw_text,
                ctx=ctx,
                quantulum_errors=quantulum_errors,
            )
            if cache and cache.quantity_grams is not None:
                cached_count += 1

            update = _build_update(
                base=base,
                recipe_id=recipe_id,
                raw_text=raw_text,
                servings_count=servings_count,
                ctx=ctx,
            )

            if update.ingredient_canonical is None and use_llm:
                llm_inputs.append((raw_text, update))
            else:
                pending.append((update, raw_text))

        if llm_inputs:
            llm_used, llm_invalid = _run_llm_batches(
                llm=llm,
                inputs=llm_inputs,
                ctx=ctx,
                pending=pending,
                batch_size=settings.llm.batch_size,
            )

        for update, _ in pending:
            write_parse_update(conn, update)

        resolved_subs = resolve_sub_recipes(conn)
        log.info("parse.sub_recipes_resolved", resolved=resolved_subs)

        record_metric(conn, MetricName.PARSE_TOTAL, total, correlation_id=correlation_id)
        record_metric(conn, MetricName.PARSE_CACHED, cached_count, correlation_id=correlation_id)
        record_metric(conn, MetricName.PARSE_LLM_USED, llm_used, correlation_id=correlation_id)
        record_metric(
            conn,
            MetricName.PARSE_LLM_INVALID_JSON,
            llm_invalid,
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.PARSE_QUANTULUM_ERRORS,
            quantulum_errors[0],
            correlation_id=correlation_id,
        )

    log.info(
        "parse.complete",
        total=total,
        cached=cached_count,
        llm_used=llm_used,
        invalid_json=llm_invalid,
    )
    return total


@dataclass(frozen=True)
class _BaseFields:
    ingredient_name: str
    ingredient_canonical: str | None
    quantity_value: Decimal | None
    quantity_unit: str | None
    quantity_grams: Decimal | None
    food_group: str | None


def _from_cache_or_compute(
    cache: CachedParse | None,
    *,
    raw_text: str,
    ctx: ParseContext,
    quantulum_errors: list[int],
) -> _BaseFields:
    if cache and cache.quantity_grams is not None and cache.ingredient_name:
        return _BaseFields(
            ingredient_name=cache.ingredient_name,
            ingredient_canonical=cache.ingredient_canonical,
            quantity_value=cache.quantity_value,
            quantity_unit=cache.quantity_unit,
            quantity_grams=cache.quantity_grams,
            food_group=cache.food_group,
        )
    parse_text = _primary_clause(raw_text)
    qty_value, qty_unit = _quantulum_then_regex(_strip_containers(parse_text), quantulum_errors)
    ingredient_name = strip_quantity(parse_text) or parse_text
    canonical, food_group = _resolve_canonical(parse_text, ingredient_name, ctx)
    grams = ctx.units.to_grams(qty_value, qty_unit, canonical)
    return _BaseFields(
        ingredient_name=ingredient_name,
        ingredient_canonical=canonical,
        quantity_value=qty_value,
        quantity_unit=qty_unit,
        quantity_grams=grams,
        food_group=food_group,
    )


def _build_update(
    *,
    base: _BaseFields,
    recipe_id: int,
    raw_text: str,
    servings_count: Decimal | None,
    ctx: ParseContext,
) -> ParseUpdate:
    per_serving: Decimal | None = None
    if base.quantity_grams is not None:
        # Recipes with no servings count are treated as a single serving so
        # per-serving grams (and therefore nutrition) still compute.
        servings = servings_count if servings_count and servings_count > 0 else Decimal(1)
        per_serving = base.quantity_grams / servings
    portions, met = _portions(per_serving, base.food_group, ctx.portion_sizes)
    return ParseUpdate(
        recipe_id=recipe_id,
        raw_text=raw_text,
        ingredient_name=base.ingredient_name,
        ingredient_canonical=base.ingredient_canonical,
        quantity_value=base.quantity_value,
        quantity_unit=base.quantity_unit,
        quantity_grams=base.quantity_grams,
        per_serving_grams=per_serving,
        food_group=base.food_group,
        portions=portions,
        portion_met=met,
    )


def _run_llm_batches(
    *,
    llm: LLMClient,
    inputs: list[tuple[str, ParseUpdate]],
    ctx: ParseContext,
    pending: list[tuple[ParseUpdate, str | None]],
    batch_size: int,
) -> tuple[int, int]:
    used = 0
    invalid = 0
    by_raw: dict[str, ParseUpdate] = {raw: update for raw, update in inputs}
    raws = list(by_raw.keys())
    for start in range(0, len(raws), batch_size):
        batch = raws[start : start + batch_size]
        try:
            response = llm.parse_lines(batch, ctx.group_names)
        except Exception as exc:
            log.warning("parse.llm_error", error=str(exc), batch_size=len(batch))
            for raw in batch:
                pending.append((by_raw[raw], raw))
            invalid += len(batch)
            continue
        items = {item.raw_text: item for item in response.items}
        for raw in batch:
            update = by_raw[raw]
            parsed = items.get(raw)
            if parsed is None:
                pending.append((update, raw))
                continue
            used += 1
            ingredient_name = parsed.ingredient_name or update.ingredient_name
            canonical = (
                normalize_text(ingredient_name) if ingredient_name else update.ingredient_canonical
            )
            food_group = parsed.food_group or update.food_group
            quantity_value = parsed.quantity_value or update.quantity_value
            quantity_unit = parsed.quantity_unit or update.quantity_unit
            grams = (
                ctx.units.to_grams(quantity_value, quantity_unit, canonical)
                or update.quantity_grams
            )
            updated = replace(
                update,
                ingredient_name=ingredient_name,
                ingredient_canonical=canonical,
                food_group=food_group,
                quantity_value=quantity_value,
                quantity_unit=quantity_unit,
                quantity_grams=grams,
            )
            servings: Decimal | None = None
            per_serving: Decimal | None = None
            if grams is not None:
                servings = _maybe_servings(updated.recipe_id, raw, pending)
                if servings and servings > 0:
                    per_serving = grams / servings
            portions, met = _portions(per_serving, food_group, ctx.portion_sizes)
            updated = replace(
                updated, per_serving_grams=per_serving, portions=portions, portion_met=met
            )
            pending.append((updated, raw))
    return used, invalid


def _maybe_servings(
    recipe_id: int, raw_text: str, pending: list[tuple[ParseUpdate, str | None]]
) -> Decimal | None:
    for update, _ in pending:
        if update.recipe_id == recipe_id and update.per_serving_grams and update.quantity_grams:
            try:
                return update.quantity_grams / update.per_serving_grams
            except (ArithmeticError, ZeroDivisionError):
                return None
    _ = raw_text
    return None


def parse_value_from_llm(parsed: object) -> Decimal | None:
    raw = cast(Any, parsed)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
