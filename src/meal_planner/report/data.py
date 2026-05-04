from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import Engine

from meal_planner.db import get_engine


@dataclass(frozen=True)
class ReportData:
    plan_run: pd.DataFrame
    plan_meal: pd.DataFrame
    plan_day: pd.DataFrame
    plan_group: pd.DataFrame
    metrics: pd.DataFrame


def load_report_data(plan_run_id: int, *, engine: Engine | None = None) -> ReportData:
    eng = engine or get_engine()
    plan_run = pd.read_sql(
        """
        SELECT plan_run_id, run_time, status, solver_status, solver_seconds, slack_total,
               total_kcal, total_fiber, relaxation_level, correlation_id, config_id
        FROM meal_planning.plan_run WHERE plan_run_id = %(pr)s
        """,
        eng,
        params={"pr": plan_run_id},
    )
    plan_meal = pd.read_sql(
        """
        SELECT pm.day, pm.meal_type, pm.recipe_id, r.title
        FROM meal_planning.plan_meal pm
        LEFT JOIN meal_planning.recipe r ON r.recipe_id = pm.recipe_id
        WHERE pm.plan_run_id = %(pr)s
        ORDER BY pm.day, pm.meal_type
        """,
        eng,
        params={"pr": plan_run_id},
    )
    plan_day = pd.read_sql(
        """
        SELECT day, kcal, fiber_g
        FROM meal_planning.plan_day
        WHERE plan_run_id = %(pr)s
        ORDER BY day
        """,
        eng,
        params={"pr": plan_run_id},
    )
    plan_group = pd.read_sql(
        """
        SELECT day, food_group, daily_count, daily_portions
        FROM meal_planning.plan_day_group
        WHERE plan_run_id = %(pr)s
        ORDER BY food_group, day
        """,
        eng,
        params={"pr": plan_run_id},
    )
    metrics = pd.read_sql(
        """
        SELECT metric_name, metric_value
        FROM meal_planning.pipeline_metric
        WHERE plan_run_id = %(pr)s OR correlation_id = (
            SELECT correlation_id FROM meal_planning.plan_run WHERE plan_run_id = %(pr)s
        )
        ORDER BY metric_time DESC
        """,
        eng,
        params={"pr": plan_run_id},
    )
    return ReportData(plan_run, plan_meal, plan_day, plan_group, metrics)
