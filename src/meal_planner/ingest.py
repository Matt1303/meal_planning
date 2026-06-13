from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from sqlalchemy import Engine, text

from meal_planner.config import Settings, SourceSelectors
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine, wait_for_db
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.recipes_repo import (
    insert_raw_ingredient_lines,
    replace_meal_types,
    upsert_recipe,
    upsert_recipe_source,
)
from meal_planner.logging import get_logger
from meal_planner.meal_types import normalize_meal_types
from meal_planner.metrics import MetricName
from meal_planner.plant import PlantClassifier
from meal_planner.servings import parse_servings_count

log = get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    files_seen: int
    recipes_upserted: int
    ingredients_inserted: int
    non_plant_filtered: int


def ingest_local_html(settings: Settings, *, engine: Engine | None = None) -> IngestResult:
    base_path = settings.sources.local_html.path
    if not base_path.exists():
        raise FileNotFoundError(f"recipe path not found: {base_path}")

    selectors = settings.sources.local_html.selectors
    classifier = PlantClassifier.from_path(settings.parse.non_plant_terms_path)

    eng = engine or get_engine()
    if not wait_for_db(eng):
        raise RuntimeError("database not ready")

    correlation_id = current_correlation_id()
    files = sorted(p for p in base_path.iterdir() if p.suffix.lower() == ".html")

    if files:
        _validate_selectors(eng, files[0], selectors, correlation_id=correlation_id)

    repo_root = Path.cwd()
    recipes_count = 0
    ingredients_count = 0
    non_plant = 0

    with eng.begin() as conn:
        for fpath in files:
            raw_html = fpath.read_text(encoding="utf-8")
            soup = BeautifulSoup(raw_html, "html.parser")
            title = _text(soup, selectors.title)
            if not title:
                log.warning("ingest.skip_no_title", file=str(fpath))
                continue

            categories = _text(soup, selectors.category)
            rating_attr = _attr(soup, selectors.rating, "value")
            servings = _text(soup, selectors.servings)
            difficulty = _text(soup, selectors.difficulty)

            rating_val: Decimal | None = None
            if rating_attr:
                try:
                    rating_val = Decimal(rating_attr)
                except (ArithmeticError, ValueError):
                    rating_val = None

            servings_count = parse_servings_count(servings)
            last_modified = datetime.fromtimestamp(fpath.stat().st_mtime, tz=UTC)
            ingredient_lines = _ingredient_lines(soup, selectors.ingredient_lines)
            declared = _parse_declared_nutrition(soup, selectors.nutrition)

            haystack = " ".join([title or "", *ingredient_lines])
            is_plant = classifier.is_plant(haystack)
            if not is_plant:
                non_plant += 1

            try:
                relative_source = str(fpath.resolve().relative_to(repo_root))
            except ValueError:
                relative_source = str(fpath)

            recipe_id = upsert_recipe(
                conn,
                title=title,
                rating=rating_val,
                servings=servings or None,
                servings_count=servings_count,
                difficulty=difficulty or None,
                categories=categories or None,
                source=relative_source,
                last_modified=last_modified,
                is_plant_based=is_plant,
                declared_kcal=declared.kcal,
                declared_protein_g=declared.protein_g,
                declared_fiber_g=declared.fiber_g,
                declared_fat_g=declared.fat_g,
                declared_carbs_g=declared.carbs_g,
            )
            upsert_recipe_source(
                conn,
                recipe_id=recipe_id,
                source_path=relative_source,
                raw_html=raw_html,
            )
            normalized = normalize_meal_types(categories)
            replace_meal_types(conn, recipe_id=recipe_id, meal_types=normalized)
            inserted = insert_raw_ingredient_lines(
                conn, recipe_id=recipe_id, lines=ingredient_lines
            )

            recipes_count += 1
            ingredients_count += inserted

        record_metric(
            conn,
            MetricName.INGEST_RECIPES,
            recipes_count,
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.INGEST_INGREDIENTS,
            ingredients_count,
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.INGEST_NON_PLANT_FILTERED,
            non_plant,
            correlation_id=correlation_id,
        )

    log.info(
        "ingest.complete",
        files=len(files),
        recipes=recipes_count,
        ingredients=ingredients_count,
        non_plant=non_plant,
    )
    return IngestResult(
        files_seen=len(files),
        recipes_upserted=recipes_count,
        ingredients_inserted=ingredients_count,
        non_plant_filtered=non_plant,
    )


def _text(soup: BeautifulSoup, selector: str) -> str:
    if not selector:
        return ""
    el = soup.select_one(selector)
    if isinstance(el, Tag):
        return str(el.get_text(strip=True))
    return ""


@dataclass(frozen=True)
class DeclaredNutrition:
    kcal: Decimal | None = None
    protein_g: Decimal | None = None
    fiber_g: Decimal | None = None
    fat_g: Decimal | None = None
    carbs_g: Decimal | None = None

    @property
    def has_any(self) -> bool:
        return any(
            v is not None
            for v in (self.kcal, self.protein_g, self.fiber_g, self.fat_g, self.carbs_g)
        )


_NUTR_PATTERNS: dict[str, re.Pattern[str]] = {
    "kcal": re.compile(r"(\d+(?:\.\d+)?)\s*(?:kcal|calories|cals?)\b", re.IGNORECASE),
    "protein_g": re.compile(r"(\d+(?:\.\d+)?)\s*g\s*protein", re.IGNORECASE),
    "fiber_g": re.compile(r"(\d+(?:\.\d+)?)\s*g\s*(?:fibre|fiber)", re.IGNORECASE),
    "fat_g": re.compile(r"(\d+(?:\.\d+)?)\s*g\s*(?:total\s*)?fat", re.IGNORECASE),
    "carbs_g": re.compile(r"(\d+(?:\.\d+)?)\s*g\s*carb", re.IGNORECASE),
}


def _parse_declared_nutrition(soup: BeautifulSoup, selector: str) -> DeclaredNutrition:
    """Parse Paprika's free-text Nutrition section, e.g.
    "592 calories  67g carbohydrate  12g fat  48g protein  13g fibre"."""
    if not selector:
        return DeclaredNutrition()
    el = soup.select_one(selector)
    if not isinstance(el, Tag):
        return DeclaredNutrition()
    text_value = el.get_text(separator=" ", strip=True)
    if not text_value:
        return DeclaredNutrition()
    values: dict[str, Decimal | None] = {}
    for field_name, pattern in _NUTR_PATTERNS.items():
        match = pattern.search(text_value)
        if match:
            try:
                values[field_name] = Decimal(match.group(1))
            except (ArithmeticError, ValueError):
                values[field_name] = None
    return DeclaredNutrition(
        kcal=values.get("kcal"),
        protein_g=values.get("protein_g"),
        fiber_g=values.get("fiber_g"),
        fat_g=values.get("fat_g"),
        carbs_g=values.get("carbs_g"),
    )


def _attr(soup: BeautifulSoup, selector: str, attr: str) -> str:
    if not selector:
        return ""
    el = soup.select_one(selector)
    if isinstance(el, Tag):
        raw_value = el.get(attr, "")
        if raw_value is None:
            return ""
        if isinstance(raw_value, list):
            return " ".join(str(item) for item in raw_value)
        return str(raw_value)
    return ""


def _ingredient_lines(soup: BeautifulSoup, selector: str) -> list[str]:
    if not selector:
        return []
    out: list[str] = []
    for p in soup.select(selector):
        if isinstance(p, Tag):
            txt = p.get_text(separator=" ", strip=True)
            if txt:
                out.append(txt)
    return out


def _validate_selectors(
    engine: Engine,
    sample_path: Path,
    selectors: SourceSelectors,
    *,
    correlation_id: str,
) -> None:
    raw = sample_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    selector_pairs: Iterable[tuple[str, str]] = (
        ("title", selectors.title),
        ("ingredient_lines", selectors.ingredient_lines),
        ("category", selectors.category),
        ("rating", selectors.rating),
        ("servings", selectors.servings),
        ("difficulty", selectors.difficulty),
    )
    unmatched: list[str] = []
    for name, selector in selector_pairs:
        if not selector:
            unmatched.append(name)
            continue
        if not soup.select(selector):
            unmatched.append(name)
    if unmatched:
        log.warning("ingest.selectors_unmatched", names=unmatched, sample=str(sample_path))
        with engine.begin() as conn:
            for name in unmatched:
                conn.execute(
                    text(
                        """
                        INSERT INTO meal_planning.pipeline_metric
                            (metric_time, metric_name, metric_value, correlation_id)
                        VALUES (now(), :n, 1, :cid)
                        """
                    ),
                    {
                        "n": f"{MetricName.INGEST_SELECTOR_UNMATCHED.value}_{name}",
                        "cid": correlation_id,
                    },
                )


def discover_html(base_path: Path) -> list[Path]:
    if not base_path.exists():
        return []
    return sorted(p for p in base_path.iterdir() if p.suffix.lower() == ".html")


def relative_to_repo(path: Path, repo_root: Path | None = None) -> str:
    root = repo_root or Path(os.getcwd())
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)
