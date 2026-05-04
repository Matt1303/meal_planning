from .ingest_html import ingest_local_html
from .source_inventory import write_source_inventory
from .backfill_history import backfill_history
from .parse_ingredients import parse_ingredients
from .nutrition import enrich_nutrition
from .optimizer import optimize_plan, write_plan
from .report import build_report


def run_all(config_path=None):
    write_source_inventory(config_path=config_path)
    ingest_local_html(config_path)
    backfill_history()
    parse_ingredients(config_path)
    enrich_nutrition(config_path)
    plan = optimize_plan(config_path)
    plan_run_id = write_plan(plan, config_path)
    build_report(plan_run_id)
    return plan_run_id


if __name__ == "__main__":
    run_all()
