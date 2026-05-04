import os
import re
import json
from quantulum3 import parser as qty_parser
from rapidfuzz import process
from sqlalchemy import text
from .db import get_engine
from .config import load_config
from .food_list import load_food_groups, load_synonyms


def _normalize_text(text_value):
    return re.sub(r"\s+", " ", text_value.strip().lower())


def _strip_quantity(text_value):
    tokens = re.split(r"\s+", text_value)
    units = {
        "g", "gram", "grams", "kg", "kilogram", "kilograms", "mg", "milligram", "milligrams",
        "ml", "millilitre", "millilitres", "l", "liter", "liters", "litre", "litres",
        "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons", "cup", "cups",
        "oz", "ounce", "ounces", "lb", "pound", "pounds",
    }
    kept = []
    for tok in tokens:
        clean = tok.strip().lower()
        if re.match(r"^[0-9/\.]+$", clean):
            continue
        if clean in units:
            continue
        kept.append(tok)
    return " ".join(kept).strip()


def _unit_to_grams(value, unit_name):
    if value is None or not unit_name:
        return None
    unit = unit_name.lower()
    if unit in ["gram", "grams", "g"]:
        return value
    if unit in ["kilogram", "kilograms", "kg"]:
        return value * 1000
    if unit in ["milligram", "milligrams", "mg"]:
        return value / 1000
    if unit in ["ounce", "ounces", "oz"]:
        return value * 28.3495
    if unit in ["pound", "pounds", "lb"]:
        return value * 453.592
    if unit in ["millilitre", "millilitres", "ml"]:
        return value
    if unit in ["liter", "liters", "litre", "litres", "l"]:
        return value * 1000
    if unit in ["teaspoon", "teaspoons", "tsp"]:
        return value * 5
    if unit in ["tablespoon", "tablespoons", "tbsp"]:
        return value * 15
    if unit in ["cup", "cups"]:
        return value * 240
    return None


def _parse_fraction(value):
    parts = value.split("/")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]) / float(parts[1])
    except Exception:
        return None


def _parse_quantity_number(value):
    text = value.strip()
    if " " in text and "/" in text:
        pieces = text.split()
        if len(pieces) == 2:
            frac = _parse_fraction(pieces[1])
            if frac is not None:
                try:
                    return float(pieces[0]) + frac
                except Exception:
                    return None
    if "/" in text:
        return _parse_fraction(text)
    try:
        return float(text)
    except Exception:
        return None


def _regex_parse_quantity(text_value):
    text = text_value.lower()
    text = text.replace("–", "-")

    range_pattern = r"(?P<q1>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:-|to)\s*(?P<q2>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>kg|g|mg|ml|l|tsp|tbsp|teaspoon|teaspoons|tablespoon|tablespoons|cup|cups|oz|ounce|ounces|lb|pound|pounds)"
    match = re.search(range_pattern, text)
    if match:
        q1 = _parse_quantity_number(match.group("q1"))
        q2 = _parse_quantity_number(match.group("q2"))
        unit = match.group("unit")
        if q1 is not None and q2 is not None:
            return (q1 + q2) / 2.0, unit

    mult_pattern = r"(?P<count>\d+)\s*x\s*(?P<q>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|mg|ml|l|oz|ounce|ounces|lb|pound|pounds)"
    match = re.search(mult_pattern, text)
    if match:
        count = _parse_quantity_number(match.group("count"))
        q = _parse_quantity_number(match.group("q"))
        unit = match.group("unit")
        if count is not None and q is not None:
            return count * q, unit

    single_pattern = r"(?P<q>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?P<unit>kg|g|mg|ml|l|tsp|tbsp|teaspoon|teaspoons|tablespoon|tablespoons|cup|cups|oz|ounce|ounces|lb|pound|pounds)"
    match = re.search(single_pattern, text)
    if match:
        q = _parse_quantity_number(match.group("q"))
        unit = match.group("unit")
        if q is not None:
            return q, unit

    return None, None


def _llm_client(cfg):
    provider = (cfg.get("llm", {}) or {}).get("provider")
    model = (cfg.get("llm", {}) or {}).get("model")
    api_key = (cfg.get("llm", {}) or {}).get("api_key") or os.getenv("LLM_API_KEY")
    if not provider or not api_key:
        return None, None, None
    return provider, model, api_key


def _llm_parse_batch(provider, model, api_key, lines, group_names):
    prompt = (
        "Extract ingredient_name, quantity_value, quantity_unit, and food_group. "
        "food_group must be one of: " + ", ".join(group_names) + ".\n"
        "Return JSON array with objects: {raw_text, ingredient_name, quantity_value, quantity_unit, food_group}."
    )
    content = prompt + "\n\n" + "\n".join(lines)
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model or "gpt-4o",
            messages=[{"role": "user", "content": content}],
            temperature=0,
        )
        return resp.choices[0].message.content
    if provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model or "claude-sonnet-4-6",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        return resp.content[0].text
    return None


def parse_ingredients(config_path=None):
    cfg = load_config(config_path)
    engine = get_engine()

    food_paths = cfg.get("food_list_paths") or [os.getenv("FOOD_LIST_PATH", "food_list.txt")]
    food_groups = load_food_groups(food_paths)
    synonyms = load_synonyms("config/ingredient_synonyms.csv")
    group_names = sorted(set(food_groups.values()))

    min_conf = (cfg.get("llm", {}) or {}).get("min_confidence", 0.8)
    portion_sizes = cfg.get("portion_sizes", {})
    provider, model, api_key = _llm_client(cfg)
    llm_batch_size = int((cfg.get("llm", {}) or {}).get("batch_size", 5))

    query = """
        SELECT ri.recipe_id, ri.raw_text, r.servings_count
        FROM meal_planning.recipe_ingredient ri
        JOIN meal_planning.recipe r ON r.recipe_id = ri.recipe_id
        WHERE ri.ingredient_canonical IS NULL
           OR ri.quantity_grams IS NULL
           OR ri.per_serving_grams IS NULL
           OR ri.food_group IS NULL
    """

    total = 0
    cached = 0
    llm_used = 0

    quantulum_ok = True

    with engine.begin() as conn:
        overrides = conn.execute(
            text("SELECT raw_text, ingredient_canonical, food_group FROM meal_planning.ingredient_override")
        ).fetchall()
        override_map = {str(r[0]).strip().lower(): (r[1], r[2]) for r in overrides}

        rows = conn.execute(text(query)).fetchall()
        updates = []
        llm_candidates = []

        for recipe_id, raw_text, servings_count in rows:
            total += 1
            cache_row = conn.execute(
                text(
                    """
                    SELECT ingredient_name, ingredient_canonical, quantity_value, quantity_unit, quantity_grams, food_group
                    FROM meal_planning.ingredient_parse_cache
                    WHERE raw_text = :raw_text
                    """
                ),
                {"raw_text": raw_text},
            ).fetchone()

            use_cache = cache_row and cache_row[4] is not None

            if use_cache:
                cached += 1
                ingredient_name, canonical, quantity_value, quantity_unit, quantity_grams, food_group = cache_row
            else:
                qty = []
                if quantulum_ok:
                    try:
                        qty = qty_parser.parse(raw_text)
                    except Exception:
                        quantulum_ok = False
                        qty = []
                quantity_value = qty[0].value if qty else None
                quantity_unit = qty[0].unit.name if qty else None
                if quantity_value is None or quantity_unit is None:
                    q_val, q_unit = _regex_parse_quantity(raw_text)
                    quantity_value = q_val if q_val is not None else quantity_value
                    quantity_unit = q_unit if q_unit is not None else quantity_unit
                quantity_grams = _unit_to_grams(quantity_value, quantity_unit)

                ingredient_name = _strip_quantity(raw_text) or raw_text
                norm = _normalize_text(raw_text)
                canonical = synonyms.get(norm)

                if _normalize_text(raw_text) in override_map:
                    canonical, food_group = override_map[_normalize_text(raw_text)]
                else:
                    if canonical is None and food_groups:
                        match, score, _ = process.extractOne(_normalize_text(ingredient_name), list(food_groups.keys()))
                        if score and (score / 100.0) >= min_conf:
                            canonical = match
                    food_group = food_groups.get(canonical) if canonical else None

            per_serving_grams = None
            portions = None
            portion_met = None
            if quantity_grams is not None and servings_count:
                try:
                    per_serving_grams = quantity_grams / float(servings_count)
                except Exception:
                    per_serving_grams = None

            if per_serving_grams is not None and food_group in portion_sizes:
                portions = per_serving_grams / float(portion_sizes[food_group])
                portion_met = per_serving_grams >= float(portion_sizes[food_group])

            payload = {
                "recipe_id": recipe_id,
                "raw_text": raw_text,
                "ingredient_name": ingredient_name,
                "ingredient_canonical": canonical,
                "quantity_value": quantity_value,
                "quantity_unit": quantity_unit,
                "quantity_grams": quantity_grams,
                "per_serving_grams": per_serving_grams,
                "food_group": food_group,
                "portions": portions,
                "portion_met": portion_met,
                "servings_count": servings_count,
            }

            if canonical is None and provider and not cache_row:
                llm_candidates.append(payload)
            else:
                updates.append(payload)

        for i in range(0, len(llm_candidates), llm_batch_size):
            batch = llm_candidates[i:i + llm_batch_size]
            lines = [item["raw_text"] for item in batch]
            raw = _llm_parse_batch(provider, model, api_key, lines, group_names)
            if raw:
                if raw.startswith("```"):
                    raw = raw.strip("`\n")
                try:
                    objs = json.loads(raw)
                except Exception:
                    objs = []
            else:
                objs = []

            for item in batch:
                match = next((o for o in objs if o.get("raw_text") == item["raw_text"]), None)
                if match:
                    item["ingredient_name"] = match.get("ingredient_name") or item["ingredient_name"]
                    item["quantity_value"] = match.get("quantity_value") or item["quantity_value"]
                    item["quantity_unit"] = match.get("quantity_unit") or item["quantity_unit"]
                    item["quantity_grams"] = _unit_to_grams(item["quantity_value"], item["quantity_unit"]) or item["quantity_grams"]
                    item["food_group"] = match.get("food_group") or item["food_group"]
                    item["ingredient_canonical"] = _normalize_text(item["ingredient_name"])
                    llm_used += 1
                if item.get("quantity_grams") is not None and item.get("servings_count"):
                    try:
                        item["per_serving_grams"] = item["quantity_grams"] / float(item["servings_count"])
                    except Exception:
                        item["per_serving_grams"] = None
                if item.get("per_serving_grams") is not None and item.get("food_group") in portion_sizes:
                    size = float(portion_sizes[item["food_group"]])
                    item["portions"] = item["per_serving_grams"] / size
                    item["portion_met"] = item["per_serving_grams"] >= size
                updates.append(item)

        for item in updates:
            update_item = {
                "recipe_id": item["recipe_id"],
                "raw_text": item["raw_text"],
                "ingredient_name": item.get("ingredient_name"),
                "ingredient_canonical": item.get("ingredient_canonical"),
                "quantity_value": item.get("quantity_value"),
                "quantity_unit": item.get("quantity_unit"),
                "quantity_grams": item.get("quantity_grams"),
                "per_serving_grams": item.get("per_serving_grams"),
                "food_group": item.get("food_group"),
                "portions": item.get("portions"),
                "portion_met": item.get("portion_met"),
            }
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
                update_item,
            )

            if update_item.get("ingredient_name"):
                conn.execute(
                    text(
                        """
                        INSERT INTO meal_planning.ingredient_parse_cache
                        (raw_text, ingredient_name, ingredient_canonical, quantity_value, quantity_unit, quantity_grams, food_group)
                        VALUES (:raw_text, :ingredient_name, :ingredient_canonical, :quantity_value, :quantity_unit, :quantity_grams, :food_group)
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
                    update_item,
                )

        conn.execute(
            text(
                "INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"
            ),
            {"name": "parse_total", "value": total},
        )
        conn.execute(
            text(
                "INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"
            ),
            {"name": "parse_cached", "value": cached},
        )
        conn.execute(
            text(
                "INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"
            ),
            {"name": "parse_llm_used", "value": llm_used},
        )

    return total
