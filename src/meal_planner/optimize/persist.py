from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import Engine

from meal_planner.config import Settings, settings_to_redacted_dict
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.plan_repo import (
    insert_meal_history,
    insert_plan_config,
    insert_plan_day,
    insert_plan_day_group,
    insert_plan_meal,
    insert_plan_run,
    update_plan_totals,
)
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName
from meal_planner.optimize.run import OptimizeResult

log = get_logger(__name__)


def write_plan(settings: Settings, result: OptimizeResult, *, engine: Engine | None = None) -> int:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    run_time = datetime.now(UTC)

    with eng.begin() as conn:
        config_id = insert_plan_config(
            conn,
            name=f"run_{run_time.isoformat()}",
            payload=settings_to_redacted_dict(settings),
            optimizer=settings.optimizer.model_dump(mode="json"),
        )
        plan_run_id = insert_plan_run(
            conn,
            run_time=run_time,
            config_id=config_id,
            status="ok",
            solver_status=result.solver_status,
            solver_seconds=result.solver_seconds,
            slack_total=result.slack_total,
            relaxation_level=result.relaxation_level,
            correlation_id=correlation_id,
        )

        nutrition = pd.read_sql(
            "SELECT recipe_id, per_serving_kcal, per_serving_fiber_g FROM meal_planning.recipe_nutrition",
            conn,
        ).set_index("recipe_id")

        ingredients = pd.read_sql(
            """
            SELECT recipe_id, ingredient_canonical, food_group, portions, portion_met
            FROM meal_planning.recipe_ingredient
            WHERE ingredient_canonical IS NOT NULL AND food_group IS NOT NULL
            """,
            conn,
        )

        targets = settings.daily_dozen_targets
        total_kcal = Decimal(0)
        total_fiber = Decimal(0)
        daily_violations = 0

        for day, meals in result.plan.items():
            day_kcal = Decimal(0)
            day_fiber = Decimal(0)
            selected = [r for r in meals.values() if r is not None]
            for meal_type, recipe_id in meals.items():
                insert_plan_meal(
                    conn,
                    plan_run_id=plan_run_id,
                    day=day,
                    meal_type=meal_type,
                    recipe_id=recipe_id,
                )
            for r in selected:
                if r in nutrition.index:
                    kcal_value = nutrition.loc[r, "per_serving_kcal"]
                    fiber_value = nutrition.loc[r, "per_serving_fiber_g"]
                    if kcal_value is not None and not pd.isna(kcal_value):
                        day_kcal += Decimal(str(kcal_value))
                    if fiber_value is not None and not pd.isna(fiber_value):
                        day_fiber += Decimal(str(fiber_value))
            insert_plan_day(
                conn, plan_run_id=plan_run_id, day=day, kcal=day_kcal, fiber_g=day_fiber
            )
            total_kcal += day_kcal
            total_fiber += day_fiber

            day_ingredients = ingredients[ingredients["recipe_id"].isin(selected)]
            day_ingredients = day_ingredients[day_ingredients["portion_met"].astype(bool)]
            grouped = day_ingredients.groupby("food_group") if not day_ingredients.empty else None

            for group in targets:
                if grouped is not None and group in grouped.groups:
                    subset = grouped.get_group(group)
                    daily_count = int(subset["ingredient_canonical"].nunique())
                    portions_total = subset["portions"].fillna(0).sum()
                    daily_portions = Decimal(str(float(portions_total)))
                else:
                    daily_count = 0
                    daily_portions = Decimal(0)
                if daily_count < int(targets[group]):
                    daily_violations += int(targets[group]) - daily_count
                insert_plan_day_group(
                    conn,
                    plan_run_id=plan_run_id,
                    day=day,
                    food_group=group,
                    daily_count=daily_count,
                    daily_portions=daily_portions,
                )

            for recipe_id in selected:
                meal_type_for_recipe = next(
                    (mt for mt, rid in meals.items() if rid == recipe_id), None
                )
                if meal_type_for_recipe is not None:
                    insert_meal_history(
                        conn,
                        recipe_id=recipe_id,
                        meal_type=meal_type_for_recipe,
                        planned_for=date.today(),
                    )

        update_plan_totals(
            conn, plan_run_id=plan_run_id, total_kcal=total_kcal, total_fiber=total_fiber
        )

        all_recipe_ids = [
            r for day_meals in result.plan.values() for r in day_meals.values() if r is not None
        ]
        all_selected = ingredients[ingredients["recipe_id"].isin(all_recipe_ids)]
        unique_ingredients = int(all_selected["ingredient_canonical"].nunique())
        unique_groups = int(all_selected["food_group"].nunique())
        record_metric(
            conn,
            MetricName.PLAN_UNIQUE_INGREDIENTS,
            unique_ingredients,
            plan_run_id=plan_run_id,
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.PLAN_UNIQUE_FOOD_GROUPS,
            unique_groups,
            plan_run_id=plan_run_id,
            correlation_id=correlation_id,
        )
        record_metric(
            conn,
            MetricName.PLAN_DAILY_DOZEN_VIOLATIONS,
            daily_violations,
            plan_run_id=plan_run_id,
            correlation_id=correlation_id,
        )

    log.info(
        "optimize.persisted",
        plan_run_id=plan_run_id,
        unique_ingredients=unique_ingredients,
        violations=daily_violations,
    )
    return plan_run_id
