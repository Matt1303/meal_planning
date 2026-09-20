"""Per-person dietary regimes: ingredient swaps and recipe eligibility.

A plant-based catalogue is keto-hostile by construction — beans, grains and
lentils. Measured on the current 46 lunch/dinner dishes: none are keto-eligible
as written, 15 clear a 20 g carb cap once starches are swapped for low-carb
stand-ins, and only 7 of those also clear a 55% fat bar. An 8-day paired plan
needs 8 distinct dishes, so eligibility filters on carbs alone and fat is left
to the daily target, where oils, avocado and snacks can meet it.

Swaps are per-person: Matt eats the rice, Ellie eats cauliflower rice from the
same pot of curry. That makes a recipe's macros profile-dependent, which is why
this returns per-(recipe, profile) deltas rather than rewriting the recipe.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd

from meal_planner.config import ProfileTargets, Settings
from meal_planner.logging import get_logger

log = get_logger(__name__)

SWAP_HEADER = (
    "from_canonical",
    "to_name",
    "kcal_per_100g",
    "protein_g_per_100g",
    "fat_g_per_100g",
    "carbs_g_per_100g",
    "fiber_g_per_100g",
    "gram_ratio",
    "note",
)

# (kcal, fiber, protein, fat, carbs) — the order persist.py and the model use.
MacroTuple = tuple[float, float, float, float, float]

# Positions within a macro tuple.
KCAL, FIBER, PROTEIN, FAT, CARBS = range(5)


@dataclass(frozen=True)
class IngredientSwap:
    from_canonical: str
    to_name: str
    kcal_per_100g: float
    protein_g_per_100g: float
    fat_g_per_100g: float
    carbs_g_per_100g: float
    fiber_g_per_100g: float
    gram_ratio: float = 1.0
    note: str = ""


def _finite(value: object, default: float = 0.0) -> float:
    """A usable number, or the default.

    `float(x) or 0` is not enough: NaN is truthy, so a NULL macro in the cache
    used to survive as NaN, poison a swap delta, and reach the solver as a NaN
    coefficient — which HiGHS rejects by silently dropping every row.
    """
    if value is None:
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def _csv_number(row: dict[str, str | None], key: str, default: float = 0.0) -> float:
    try:
        return float((row.get(key) or "").strip() or default)
    except ValueError:
        return default


def load_swaps(path: Path) -> dict[str, IngredientSwap]:
    """canonical -> substitute. Missing file means no swaps, not an error."""
    if not path.exists():
        return {}
    out: dict[str, IngredientSwap] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            source = (row.get("from_canonical") or "").strip().lower()
            target = (row.get("to_name") or "").strip()
            if not source or not target:
                continue

            out[source] = IngredientSwap(
                from_canonical=source,
                to_name=target,
                kcal_per_100g=_csv_number(row, "kcal_per_100g"),
                protein_g_per_100g=_csv_number(row, "protein_g_per_100g"),
                fat_g_per_100g=_csv_number(row, "fat_g_per_100g"),
                carbs_g_per_100g=_csv_number(row, "carbs_g_per_100g"),
                fiber_g_per_100g=_csv_number(row, "fiber_g_per_100g"),
                gram_ratio=_csv_number(row, "gram_ratio", 1.0) or 1.0,
                note=(row.get("note") or "").strip(),
            )
    return out


def save_swaps(path: Path, swaps: dict[str, IngredientSwap]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(SWAP_HEADER)
        for swap in sorted(swaps.values(), key=lambda s: s.from_canonical):
            writer.writerow(
                [
                    swap.from_canonical,
                    swap.to_name,
                    swap.kcal_per_100g,
                    swap.protein_g_per_100g,
                    swap.fat_g_per_100g,
                    swap.carbs_g_per_100g,
                    swap.fiber_g_per_100g,
                    swap.gram_ratio,
                    swap.note,
                ]
            )
    tmp.replace(path)


@dataclass(frozen=True)
class DietAdjustments:
    """What a regime does to one person's view of the recipe catalogue."""

    # (recipe_id, profile_name) -> macro delta to add to the recipe's own values.
    deltas: dict[tuple[int, str], MacroTuple]
    # profile_name -> recipe ids that person may be served.
    eligible: dict[str, set[int]]
    # (recipe_id, profile_name) -> swap descriptions, for the plan view.
    swap_notes: dict[tuple[int, str], list[str]]

    @property
    def active(self) -> bool:
        return bool(self.eligible)


def _profile_swaps(
    profile: ProfileTargets, swaps: dict[str, IngredientSwap]
) -> dict[str, IngredientSwap]:
    if profile.diet is None or not profile.diet.swaps_enabled:
        return {}
    return swaps


def compute_diet_adjustments(
    ingredients: pd.DataFrame,
    nutrition: pd.DataFrame,
    settings: Settings,
    swaps: dict[str, IngredientSwap],
) -> DietAdjustments:
    """Per-person macro deltas and eligible-recipe sets.

    Returns empty structures when nobody is on a regime, so the optimiser is
    untouched in the normal case.
    """
    dieters = [p for p in settings.household.profiles if p.diet is not None]
    if not dieters:
        return DietAdjustments(deltas={}, eligible={}, swap_notes={})

    # Base per-serving macros in the (kcal, fiber, protein, fat, carbs) order
    # the deltas use, so a swap can be clamped against what is actually there.
    columns = (
        "per_serving_kcal",
        "per_serving_fiber_g",
        "per_serving_protein_g",
        "per_serving_fat_g",
        "per_serving_carbs_g",
    )
    base_by_recipe: dict[int, list[float]] = {}
    if not nutrition.empty:
        for row in nutrition.itertuples():
            values = []
            for column in columns:
                value = getattr(row, column, None)
                values.append(0.0 if value is None or pd.isna(value) else float(value))
            base_by_recipe[int(row.recipe_id)] = values
    carbs_by_recipe = {r: v[CARBS] for r, v in base_by_recipe.items()}

    deltas: dict[tuple[int, str], MacroTuple] = {}
    swap_notes: dict[tuple[int, str], list[str]] = {}
    eligible: dict[str, set[int]] = {}

    for profile in dieters:
        rules = profile.diet
        assert rules is not None
        active_swaps = _profile_swaps(profile, swaps)
        swapped_carbs: dict[int, float] = dict(carbs_by_recipe)

        if active_swaps and not ingredients.empty:
            for row in ingredients.itertuples():
                canonical = str(getattr(row, "ingredient_canonical", "") or "").lower()
                swap = active_swaps.get(canonical)
                grams = getattr(row, "per_serving_grams", None)
                if swap is None or grams is None or pd.isna(grams):
                    continue
                recipe_id = int(row.recipe_id)
                old_g = float(grams)
                new_g = old_g * swap.gram_ratio
                old = (
                    _finite(getattr(row, "kcal_per_100g", None)),
                    _finite(getattr(row, "fiber_g_per_100g", None)),
                    _finite(getattr(row, "protein_g_per_100g", None)),
                    _finite(getattr(row, "fat_g_per_100g", None)),
                    _finite(getattr(row, "carbs_g_per_100g", None)),
                )
                new = (
                    swap.kcal_per_100g,
                    swap.fiber_g_per_100g,
                    swap.protein_g_per_100g,
                    swap.fat_g_per_100g,
                    swap.carbs_g_per_100g,
                )
                delta = tuple((new[i] * new_g - old[i] * old_g) / 100.0 for i in range(5))
                key = (recipe_id, profile.name)
                running = deltas.get(key, (0.0, 0.0, 0.0, 0.0, 0.0))
                deltas[key] = (
                    running[0] + delta[0],
                    running[1] + delta[1],
                    running[2] + delta[2],
                    running[3] + delta[3],
                    running[4] + delta[4],
                )
                swap_notes.setdefault(key, []).append(f"{canonical} → {swap.to_name}")
                swapped_carbs[recipe_id] = swapped_carbs.get(recipe_id, 0.0) + delta[4]

        for (recipe_id, name), delta in list(deltas.items()):
            if name != profile.name:
                continue
            base = base_by_recipe.get(recipe_id)
            if base is None:
                continue
            clamped = tuple(max(delta[i], -base[i]) for i in range(5))
            if clamped != delta:
                # A declared-nutrition recipe carries an external total that is
                # not the sum of its lines, so removing an ingredient in full
                # can overshoot it. Never take a macro below zero.
                log.debug("diet.delta_clamped", recipe_id=recipe_id, profile=name)
                deltas[(recipe_id, name)] = cast(MacroTuple, clamped)
                swapped_carbs[recipe_id] = base[CARBS] + clamped[CARBS]

        allowed = {
            recipe_id
            for recipe_id, carbs in swapped_carbs.items()
            if carbs <= rules.max_carbs_per_serving
        }
        eligible[profile.name] = allowed
        log.info(
            "diet.eligibility",
            profile=profile.name,
            regime=rules.name,
            eligible=len(allowed),
            of=len(swapped_carbs),
            swapped_recipes=len({r for r, _ in deltas if _ == profile.name}),
        )

    return DietAdjustments(deltas=deltas, eligible=eligible, swap_notes=swap_notes)
