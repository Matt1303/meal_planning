from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

VOLUME_UNITS: frozenset[str] = frozenset(
    {"ml", "millilitre", "millilitres", "l", "litre", "litres", "liter", "liters", "cup", "cups"}
)

PIECE_UNIT_TOKENS: frozenset[str] = frozenset(
    {"piece", "pieces", "each", "whole", "count", "item", "items", "unit", "units"}
)

UNIT_ALIASES: dict[str, str] = {
    "cubic centimetre": "millilitre",
    "cubic centimetres": "millilitres",
    "cm^3": "millilitre",
    "cc": "millilitre",
}


class UnitTable:
    def __init__(
        self,
        unit_grams: dict[str, Decimal],
        density_g_per_ml: dict[str, Decimal],
        piece_grams: dict[str, Decimal] | None = None,
    ) -> None:
        self._unit_grams = {k.lower(): v for k, v in unit_grams.items()}
        self._density = {k.lower(): v for k, v in density_g_per_ml.items()}
        self._piece_grams = {k.lower(): v for k, v in (piece_grams or {}).items()}

    @classmethod
    def from_paths(
        cls,
        units_path: Path,
        density_path: Path,
        piece_path: Path | None = None,
    ) -> UnitTable:
        return cls(
            unit_grams=_load_unit_csv(units_path),
            density_g_per_ml=_load_density_csv(density_path),
            piece_grams=_load_piece_csv(piece_path) if piece_path is not None else {},
        )

    def to_grams(
        self, value: Decimal | float | None, unit: str | None, ingredient: str | None = None
    ) -> Decimal | None:
        if value is None:
            return None
        unit_l = (unit or "").strip().lower()
        unit_l = UNIT_ALIASES.get(unit_l, unit_l)
        if unit_l and unit_l in self._unit_grams:
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
        if ingredient and (not unit_l or unit_l in PIECE_UNIT_TOKENS):
            piece = self._piece_grams.get(ingredient.strip().lower())
            if piece is not None:
                return Decimal(str(value)) * piece
        return None

    def known_unit(self, unit: str | None) -> bool:
        if not unit:
            return False
        unit_l = unit.strip().lower()
        unit_l = UNIT_ALIASES.get(unit_l, unit_l)
        return unit_l in self._unit_grams

    def piece_grams(self, ingredient: str | None) -> Decimal | None:
        if not ingredient:
            return None
        return self._piece_grams.get(ingredient.strip().lower())


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


def _load_piece_csv(path: Path | None) -> dict[str, Decimal]:
    if path is None or not path.exists():
        return {}
    out: dict[str, Decimal] = {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ingredient = (row.get("ingredient_canonical") or "").strip().lower()
            grams = (row.get("grams_per_piece") or "").strip()
            if ingredient and grams:
                try:
                    out[ingredient] = Decimal(grams)
                except (ArithmeticError, ValueError):
                    continue
    return out
