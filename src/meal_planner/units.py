from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

VOLUME_UNITS: frozenset[str] = frozenset(
    {"ml", "millilitre", "millilitres", "l", "litre", "litres", "liter", "liters", "cup", "cups"}
)


class UnitTable:
    def __init__(
        self,
        unit_grams: dict[str, Decimal],
        density_g_per_ml: dict[str, Decimal],
    ) -> None:
        self._unit_grams = {k.lower(): v for k, v in unit_grams.items()}
        self._density = {k.lower(): v for k, v in density_g_per_ml.items()}

    @classmethod
    def from_paths(cls, units_path: Path, density_path: Path) -> UnitTable:
        return cls(
            unit_grams=_load_unit_csv(units_path),
            density_g_per_ml=_load_density_csv(density_path),
        )

    def to_grams(
        self, value: Decimal | float | None, unit: str | None, ingredient: str | None = None
    ) -> Decimal | None:
        if value is None or unit is None:
            return None
        unit_l = unit.strip().lower()
        if unit_l not in self._unit_grams:
            return None
        base = self._unit_grams[unit_l]
        grams = Decimal(str(value)) * base
        if unit_l in VOLUME_UNITS and ingredient:
            density = self._density.get(ingredient.strip().lower())
            if density is not None and density > 0:
                if unit_l in {"l", "litre", "litres", "liter", "liters"}:
                    ml_value = Decimal(str(value)) * Decimal(1000)
                elif unit_l in {"cup", "cups"}:
                    ml_value = Decimal(str(value)) * Decimal(240)
                else:
                    ml_value = Decimal(str(value))
                grams = ml_value * density
        return grams

    def known_unit(self, unit: str | None) -> bool:
        if not unit:
            return False
        return unit.strip().lower() in self._unit_grams


def _load_unit_csv(path: Path) -> dict[str, Decimal]:
    if not path.exists():
        return {}
    out: dict[str, Decimal] = {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            unit = (row.get("unit") or "").strip().lower()
            grams = (row.get("grams_per_unit") or "").strip()
            if unit and grams:
                try:
                    out[unit] = Decimal(grams)
                except (ArithmeticError, ValueError):
                    continue
    return out


def _load_density_csv(path: Path) -> dict[str, Decimal]:
    if not path.exists():
        return {}
    out: dict[str, Decimal] = {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ingredient = (row.get("ingredient_canonical") or "").strip().lower()
            density = (row.get("density") or "").strip()
            if ingredient and density:
                try:
                    out[ingredient] = Decimal(density)
                except (ArithmeticError, ValueError):
                    continue
    return out
