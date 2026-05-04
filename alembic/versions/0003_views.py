"""create reporting views

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.pipeline_run AS
        SELECT pr.plan_run_id,
               pr.run_time,
               pr.status,
               pr.solver_status,
               pr.solver_seconds,
               pr.slack_total,
               pr.relaxation_level,
               pr.correlation_id,
               (SELECT metric_value FROM {SCHEMA}.pipeline_metric pm
                  WHERE pm.metric_name = 'parse_cached'
                    AND pm.correlation_id = pr.correlation_id
                  ORDER BY pm.metric_time DESC LIMIT 1) AS parse_cached,
               (SELECT metric_value FROM {SCHEMA}.pipeline_metric pm
                  WHERE pm.metric_name = 'parse_total'
                    AND pm.correlation_id = pr.correlation_id
                  ORDER BY pm.metric_time DESC LIMIT 1) AS parse_total,
               (SELECT metric_value FROM {SCHEMA}.pipeline_metric pm
                  WHERE pm.metric_name = 'nutrition_coverage_ratio'
                    AND pm.correlation_id = pr.correlation_id
                  ORDER BY pm.metric_time DESC LIMIT 1) AS nutrition_coverage_ratio
        FROM {SCHEMA}.plan_run pr
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.latest_plan_summary AS
        SELECT pm.day,
               pm.meal_type,
               pm.recipe_id,
               r.title,
               pd.kcal,
               pd.fiber_g
        FROM {SCHEMA}.plan_meal pm
        LEFT JOIN {SCHEMA}.recipe r ON r.recipe_id = pm.recipe_id
        LEFT JOIN {SCHEMA}.plan_day pd ON pd.plan_run_id = pm.plan_run_id AND pd.day = pm.day
        WHERE pm.plan_run_id = (SELECT plan_run_id FROM {SCHEMA}.plan_run ORDER BY run_time DESC LIMIT 1)
        ORDER BY pm.day, pm.meal_type
        """
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.latest_plan_summary")
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.pipeline_run")
