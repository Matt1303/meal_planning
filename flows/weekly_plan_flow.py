from __future__ import annotations

from pathlib import Path

from prefect import flow, task
from prefect.tasks import task_input_hash

from meal_planner.config import Settings
from meal_planner.correlation import current_correlation_id, new_correlation_id, set_correlation_id
from meal_planner.db import get_engine
from meal_planner.ingest import ingest_local_html
from meal_planner.nutrition import enrich_nutrition
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.parse import parse_ingredients
from meal_planner.report import ReportFormats, generate_report
from meal_planner.source_inventory import write_source_inventory


@task(name="inventory")
def inventory_task(settings: Settings) -> Path:
    return write_source_inventory(settings)


@task(name="ingest", retries=1, retry_delay_seconds=30)
def ingest_task(settings: Settings) -> int:
    return ingest_local_html(settings, engine=get_engine()).recipes_upserted


@task(name="parse", retries=1, retry_delay_seconds=30)
def parse_task(settings: Settings) -> int:
    return parse_ingredients(settings, engine=get_engine())


@task(name="nutrition", retries=2, retry_delay_seconds=60, cache_key_fn=task_input_hash)
def nutrition_task(settings: Settings, ignore_coverage: bool) -> int:
    return enrich_nutrition(settings, engine=get_engine(), ignore_coverage=ignore_coverage)


@task(name="optimise")
def optimise_task(settings: Settings) -> dict[str, object]:
    result = optimize_plan(settings, engine=get_engine())
    plan_run_id = write_plan(settings, result, engine=get_engine())
    return {
        "plan_run_id": plan_run_id,
        "relaxation_level": result.relaxation_level,
    }


@task(name="report")
def report_task(plan_run_id: int) -> dict[str, str]:
    outputs = generate_report(
        plan_run_id,
        engine=get_engine(),
        formats=ReportFormats(md=True, html=True, docx=False),
    )
    return {k: str(v) for k, v in outputs.items()}


@flow(name="weekly-meal-plan")
def weekly_plan_flow(
    config_path: str = "config/pipeline.yaml", ignore_coverage: bool = False
) -> dict[str, object]:
    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    settings = Settings.load(config_path)
    inventory_task(settings)
    ingest_task(settings)
    parse_task(settings)
    nutrition_task(settings, ignore_coverage)
    optimise_outcome = optimise_task(settings)
    plan_run_id = int(optimise_outcome["plan_run_id"])  # type: ignore[arg-type]
    paths = report_task(plan_run_id)
    return {
        "plan_run_id": plan_run_id,
        "relaxation_level": optimise_outcome["relaxation_level"],
        "correlation_id": current_correlation_id(),
        "outputs": paths,
    }


if __name__ == "__main__":
    weekly_plan_flow()
