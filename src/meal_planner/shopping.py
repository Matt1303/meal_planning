from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text

from meal_planner.config import Settings

# Supermarket walk order, used for grouping + sorting the shopping list.
SECTION_ORDER = [
    "Fruit & Veg",
    "Chilled",
    "Frozen",
    "Tins & Pulses",
    "Grains & Pasta",
    "Herbs & Sauces",
    "Oils & Condiments",
    "Nuts & Baking",
    "Other",
]
DEFAULT_SECTION = "Other"

# Pantry staples / non-items that just clutter a weekly shopping list.
_NON_SHOPPING = {
    "water",
    "tap water",
    "ice",
    "ice cubes",
    "salt",
    "sea salt",
    "sea salt flakes",
    "table salt",
    "black pepper",
    "white pepper",
}

# Daily Dozen food group -> section, used when no keyword matches.
_FOOD_GROUP_SECTION = {
    "Beans": "Tins & Pulses",
    "Berries": "Fruit & Veg",
    "Other Fruits": "Fruit & Veg",
    "Cruciferous Vegetables": "Fruit & Veg",
    "Greens": "Fruit & Veg",
    "Other Vegetables": "Fruit & Veg",
    "Whole Grains": "Grains & Pasta",
    "Nuts and Seeds": "Nuts & Baking",
    "Flaxseeds or Linseeds": "Nuts & Baking",
    "Herbs and Spices": "Herbs & Sauces",
}


@dataclass(frozen=True)
class ShoppingItem:
    section: str
    ingredient_canonical: str
    total_grams: float | None
    display_text: str
    sort_order: int
    checked: bool = False


def _load_section_keywords(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    out: list[tuple[str, str]] = []
    for row in csv.DictReader(path.open(newline="")):
        keyword = (row.get("keyword") or "").strip().lower()
        section = (row.get("section") or "").strip()
        if keyword and section:
            out.append((keyword, section))
    # Longest keyword first so "coconut milk" beats "milk".
    out.sort(key=lambda kv: len(kv[0]), reverse=True)
    return out


def _section_for(canonical: str, food_group: str | None, keywords: list[tuple[str, str]]) -> str:
    low = canonical.lower()
    for keyword, section in keywords:
        if keyword in low:
            return section
    if food_group and food_group in _FOOD_GROUP_SECTION:
        return _FOOD_GROUP_SECTION[food_group]
    return DEFAULT_SECTION


def _singularize(name: str) -> str:
    """Crude singular key so e.g. 'avocado' and 'avocados' merge into one line.
    Only used as a grouping key, never displayed."""
    s = name.strip().lower()
    if s.endswith("ies") and len(s) > 4:
        return s[:-3] + "y"
    if s.endswith(("oes", "ses", "shes", "ches", "xes")):
        return s[:-2]
    if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        return s[:-1]
    return s


def _format_qty(grams: float) -> str:
    if grams >= 1000:
        return f"{grams / 1000:.1f} kg"
    if grams >= 100:
        return f"{int(round(grams / 10) * 10)} g"
    return f"{max(5, int(round(grams / 5) * 5))} g"


def build_shopping_list(
    conn: Connection, plan_run_id: int, settings: Settings
) -> list[ShoppingItem]:
    """Aggregate every ingredient in the plan into a household shopping list,
    grouped by supermarket section. Shared meals count once per person."""
    people = (
        conn.execute(
            text(
                "SELECT count(DISTINCT profile_id) FROM meal_planning.plan_meal "
                "WHERE plan_run_id = :pr AND profile_id > 0"
            ),
            {"pr": plan_run_id},
        ).scalar()
        or 1
    )
    rows = conn.execute(
        text(
            """
            WITH meals AS (
                SELECT pm.recipe_id,
                       CASE WHEN pm.profile_id = 0 THEN :people ELSE 1 END AS servings
                FROM meal_planning.plan_meal pm
                WHERE pm.plan_run_id = :pr AND pm.recipe_id IS NOT NULL
            )
            SELECT ri.ingredient_canonical,
                   max(ri.food_group) AS food_group,
                   ri.portion_estimated,
                   SUM(ri.per_serving_grams * m.servings) AS total_grams
            FROM meals m
            JOIN meal_planning.recipe_ingredient ri ON ri.recipe_id = m.recipe_id
            WHERE ri.ingredient_canonical IS NOT NULL
              AND ri.per_serving_grams IS NOT NULL
              AND ri.sub_recipe_id IS NULL
            GROUP BY ri.ingredient_canonical, ri.portion_estimated
            """
        ),
        {"pr": plan_run_id, "people": int(people)},
    ).fetchall()

    # Grams are treated as raw/uncooked purchase weights, except cooked default
    # portions (e.g. rice), which are converted to their raw weight for shopping.
    cooked_ratio: dict[str, float] = {}
    for spec in settings.optimizer.per_person_portions:
        if spec.cooked_to_raw_ratio:
            for canonical in spec.canonicals:
                cooked_ratio[canonical.strip().lower()] = spec.cooked_to_raw_ratio

    # Merge singular/plural variants (avocado + avocados) into one line, summing
    # quantities; display the dominant spelling.
    merged_grams: dict[str, float] = {}
    merged_group: dict[str, str | None] = {}
    merged_forms: dict[str, dict[str, float]] = {}
    for canonical, food_group, portion_estimated, grams in rows:
        name = str(canonical)
        if name.strip().lower() in _NON_SHOPPING:
            continue
        key = _singularize(name)
        g = float(grams) if grams is not None else 0.0
        if portion_estimated:
            ratio = cooked_ratio.get(name.strip().lower())
            if ratio:
                g /= ratio
        merged_grams[key] = merged_grams.get(key, 0.0) + g
        if merged_group.get(key) is None and food_group is not None:
            merged_group[key] = str(food_group)
        forms = merged_forms.setdefault(key, {})
        forms[name] = forms.get(name, 0.0) + g

    keywords = _load_section_keywords(settings.shopping_sections_path)
    items: list[ShoppingItem] = []
    for key, forms in merged_forms.items():
        # dominant spelling: most grams, then shortest
        name = max(forms.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        food_group = merged_group.get(key)
        total = merged_grams[key] or None
        section = _section_for(name, food_group, keywords)
        qty = _format_qty(total) if total else "—"
        order = SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)
        items.append(
            ShoppingItem(
                section=section,
                ingredient_canonical=name,
                total_grams=total,
                display_text=f"{name} — {qty}",
                sort_order=order,
            )
        )
    items.sort(key=lambda it: (it.sort_order, it.ingredient_canonical))
    return items


def fetch_shopping_list(conn: Connection, plan_run_id: int) -> list[ShoppingItem]:
    rows = conn.execute(
        text(
            """
            SELECT section, ingredient_canonical, total_grams, display_text, sort_order, checked
            FROM meal_planning.shopping_list_item
            WHERE plan_run_id = :pr
            ORDER BY sort_order, ingredient_canonical
            """
        ),
        {"pr": plan_run_id},
    ).fetchall()
    return [
        ShoppingItem(
            section=str(r[0]),
            ingredient_canonical=str(r[1]),
            total_grams=float(r[2]) if r[2] is not None else None,
            display_text=str(r[3]),
            sort_order=int(r[4]),
            checked=bool(r[5]),
        )
        for r in rows
    ]


def set_item_checked(conn: Connection, plan_run_id: int, canonical: str, checked: bool) -> None:
    conn.execute(
        text(
            """
            UPDATE meal_planning.shopping_list_item SET checked = :c
            WHERE plan_run_id = :pr AND ingredient_canonical = :ic
            """
        ),
        {"c": checked, "pr": plan_run_id, "ic": canonical},
    )


def shopping_list_markdown(items: list[ShoppingItem], heading: str) -> str:
    lines = [f"# {heading}", ""]
    for section in SECTION_ORDER:
        section_items = [it for it in items if it.section == section]
        if not section_items:
            continue
        lines.append(f"## {section}")
        for it in section_items:
            box = "x" if it.checked else " "
            lines.append(f"- [{box}] {it.display_text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
