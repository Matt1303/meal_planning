from prefect import flow, task
from pipeline.ingest_html import ingest_local_html
from pipeline.source_inventory import write_source_inventory
from pipeline.backfill_history import backfill_history
from pipeline.parse_ingredients import parse_ingredients
from pipeline.nutrition import enrich_nutrition
from pipeline.optimizer import optimize_plan, write_plan
from pipeline.report import build_report


@task
def ingest_task(config_path=None):
    return ingest_local_html(config_path)


@task
def inventory_task(config_path=None):
    return write_source_inventory(config_path=config_path)


@task
def history_task():
    return backfill_history()


@task
def parse_task(config_path=None):
    return parse_ingredients(config_path)


@task
def nutrition_task(config_path=None):
    return enrich_nutrition(config_path)


@task
def optimize_task(config_path=None):
    return optimize_plan(config_path)


@task
def write_task(plan, config_path=None):
    plan_run_id = write_plan(plan, config_path)
    build_report(plan_run_id)
    return plan_run_id


@flow
def weekly_plan_flow(config_path=None):
    inventory_task(config_path)
    ingest_task(config_path)
    history_task()
    parse_task(config_path)
    nutrition_task(config_path)
    plan = optimize_task(config_path)
    return write_task(plan, config_path)


if __name__ == "__main__":
    weekly_plan_flow()
