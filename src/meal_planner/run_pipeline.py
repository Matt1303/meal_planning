from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id, new_correlation_id, set_correlation_id
from meal_planner.db import get_engine
from meal_planner.ingest import ingest_local_html
from meal_planner.logging import get_logger
from meal_planner.nutrition import enrich_nutrition
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.parse import parse_ingredients
from meal_planner.report import ReportFormats, generate_report
from meal_planner.source_inventory import write_source_inventory

log = get_logger(__name__)


@dataclass(frozen=True)
class RunResult:
    plan_run_id: int
    correlation_id: str


def run_all(
    settings: Settings,
    *,
    engine: Engine | None = None,
    ignore_coverage: bool = False,
    formats: ReportFormats = ReportFormats(md=True, html=False, docx=False),
    output_dir: Path = Path("reports"),
) -> RunResult:
    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    eng = engine or get_engine()
    log.info("pipeline.start", correlation_id=correlation_id)

    write_source_inventory(settings)
    ingest_local_html(settings, engine=eng)
    parse_ingredients(settings, engine=eng)
    enrich_nutrition(settings, engine=eng, ignore_coverage=ignore_coverage)
    optimize_result = optimize_plan(settings, engine=eng)
    plan_run_id = write_plan(settings, optimize_result, engine=eng)
    generate_report(plan_run_id, engine=eng, output_dir=output_dir, formats=formats)
    log.info("pipeline.complete", plan_run_id=plan_run_id, correlation_id=correlation_id)
    return RunResult(plan_run_id=plan_run_id, correlation_id=current_correlation_id())
