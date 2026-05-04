from __future__ import annotations

MEAL_TYPE_NORMALIZE: dict[str, str] = {
    "breakfast": "breakfast",
    "breakfasts": "breakfast",
    "brunch": "breakfast",
    "lunch": "lunch",
    "lunches": "lunch",
    "dinner": "dinner",
    "dinners": "dinner",
    "supper": "dinner",
    "snack": "snack",
    "snacks": "snack",
}


def normalize_meal_types(raw: str | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split(","):
        token = part.strip().lower()
        canonical = MEAL_TYPE_NORMALIZE.get(token)
        if canonical and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out
