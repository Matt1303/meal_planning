from __future__ import annotations

import re
from decimal import Decimal

_RANGE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(?P<b>\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)
_DOZEN = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*dozen", re.IGNORECASE)
_NUMBER = re.compile(r"(?P<n>\d+(?:\.\d+)?)")
# A number immediately followed by a weight/volume unit is a yield
# ("Makes about 500ml"), not a serving count — strip it before parsing.
_YIELD = re.compile(
    r"\d+(?:\.\d+)?\s*"
    r"(?:ml|cl|l|litre|litres|liter|liters|g|kg|gram|grams|gramme|grammes|"
    r"oz|ounce|ounces|lb|lbs|pound|pounds)\b",
    re.IGNORECASE,
)


def parse_servings_count(value: str | None) -> Decimal | None:
    if not value:
        return None
    raw = _YIELD.sub(" ", value.strip().lower())
    if not raw.strip():
        return None

    dozen = _DOZEN.search(raw)
    if dozen:
        return Decimal(dozen.group("n")) * Decimal(12)

    rng = _RANGE.search(raw)
    if rng:
        a = Decimal(rng.group("a"))
        b = Decimal(rng.group("b"))
        return (a + b) / Decimal(2)

    num = _NUMBER.search(raw)
    if num:
        result = Decimal(num.group("n"))
        if result <= 0:
            return None
        return result

    return None
