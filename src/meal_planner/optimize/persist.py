from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import Engine

from meal_planner.config import Settings, settings_to_redacted_dict
from meal_planner.correlation import current_correlation_id
from meal_planner.db import get_engine
from meal_planner.db.metrics_repo import record_metric
from meal_planner.db.plan_repo import (
    insert_plan_config,
    insert_plan_day,
    insert_plan_day_group,
    insert_plan_day_profile,
    insert_plan_meal,
    insert_plan_run,
    update_plan_totals,
)
from meal_planner.db.profile_repo import upsert_profile
from meal_planner.logging import get_logger
from meal_planner.metrics import MetricName
from meal_planner.optimize.run import SHARED_KEY, OptimizeResult

log = get_logger(__name__)

MACRO_COLUMNS = (
    "per_serving_kcal",
    "per_serving_fiber_g",
    "per_serving_protein_g",
    "per_serving_fat_g",
    "per_serving_carbs_g",
)


def _macros(
    nutrition: pd.DataFrame, recipe_id: int
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    if recipe_id not in nutrition.index:
        return (Decimal(0),) * 5
    row = nutrition.loc[recipe_id]
    values: list[Decimal] = []
    for col in MACRO_COLUMNS:
        v = row.get(col)
        if v is None or pd.isna(v):
            values.append(Decimal(0))
        else:
            values.append(Decimal(str(v)))
    return values[0], values[1], values[2], values[3], values[4]


def write_plan(settings: Settings, result: OptimizeResult, *, engine: Engine | None = None) -> int:
    eng = engine or get_engine()
    correlation_id = current_correlation_id()
    run_time = datetime.now(UTC)

    with eng.begin() as conn:
        profile_ids: dict[str, int] = {}
        configured_by_name = {p.name: p for p in settings.household.profiles}
        for spec in result.prepared.profiles:
            profile_targets = configured_by_name.get(spec.name)
            if profile_targets is None:
                from meal_planner.config import ProfileTargets

                profile_targets = ProfileTargets(
                    name=spec.name,
                    display_name=spec.display_name,
                    calories_daily_min=spec.calories_daily_min,
                    calories_daily_max=spec.calories_daily_max,
                    fiber_daily_min=spec.fiber_daily_min,
                    protein_daily_min=spec.protein_daily_min,
                    protein_daily_max=spec.protein_daily_max,
                )
            profile_ids[spec.name] = upsert_profile(conn, profile_targets)

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
            status="draft",
            solver_status=result.solver_status,
            solver_seconds=result.solver_seconds,
            slack_total=result.slack_total,
            relaxation_level=result.relaxation_level,
            correlation_id=correlation_id,
        )

        nutrition = pd.read_sql(
            """
            SELECT recipe_id, per_serving_kcal, per_serving_fiber_g,
                   per_serving_protein_g, per_serving_fat_g, per_serving_carbs_g
            FROM meal_planning.recipe_nutrition
            """,
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
        prepared = result.prepared
        profile_names = [p.name for p in prepared.profiles]

        household_total_kcal = Decimal(0)
        household_total_fiber = Decimal(0)
        daily_violations = 0
        all_recipe_ids: list[int] = []

        for day, slot_to_cell in result.plan.items():
            for meal_type, cell in slot_to_cell.items():
                for owner, recipe_id in cell.items():
                    if recipe_id is not None:
                        all_recipe_ids.append(recipe_id)
                    profile_id = 0 if owner == SHARED_KEY else profile_ids.get(owner, 0)
                    insert_plan_meal(
                        conn,
                        plan_run_id=plan_run_id,
                        day=day,
                        meal_type=meal_type,
                        recipe_id=recipe_id,
                        profile_id=profile_id,
                    )

            shared_recipes_today: list[int] = []
            for mt in prepared.shared_meal_types:
                cell = slot_to_cell.get(mt, {})
                shared_recipe = cell.get(SHARED_KEY)
                if shared_recipe is not None:
                    shared_recipes_today.append(shared_recipe)

            per_profile_recipes: dict[str, list[int]] = {
                p: list(shared_recipes_today) for p in profile_names
            }
            for mt in prepared.per_user_meal_types:
                cell = slot_to_cell.get(mt, {})
                for owner, recipe_id in cell.items():
                    if owner == SHARED_KEY or recipe_id is None:
                        continue
                    per_profile_recipes.setdefault(owner, []).append(recipe_id)

            day_household = [Decimal(0)] * 5
            topup = settings.topup
            for profile_name in profile_names:
                profile_id = profile_ids[profile_name]
                totals = [Decimal(0)] * 5
                for r in per_profile_recipes[profile_name]:
                    macros = _macros(nutrition, r)
                    for i, v in enumerate(macros):
                        totals[i] += v
                # Whey the solver allocated for this person/day (kcal, fibre,
                # protein, fat, carbs).
                scoops = result.whey.get((profile_name, day), 0.0)
                if scoops:
                    for i, per_scoop in enumerate(
                        (
                            topup.whey_kcal,
                            topup.whey_fiber_g,
                            topup.whey_protein_g,
                            topup.whey_fat_g,
                            topup.whey_carbs_g,
                        )
                    ):
                        totals[i] += Decimal(str(scoops * per_scoop))
                insert_plan_day_profile(
                    conn,
                    plan_run_id=plan_run_id,
                    day=day,
                    profile_id=profile_id,
                    kcal=totals[0],
                    fiber_g=totals[1],
                    protein_g=totals[2],
                    fat_g=totals[3],
                    carbs_g=totals[4],
                    whey_scoops=scoops,
                )
                for i in range(5):
                    day_household[i] += totals[i]

            insert_plan_day(
                conn,
                plan_run_id=plan_run_id,
                day=day,
                kcal=day_household[0],
                fiber_g=day_household[1],
                protein_g=day_household[2],
                fat_g=day_household[3],
                carbs_g=day_household[4],
            )
            household_total_kcal += day_household[0]
            household_total_fiber += day_household[1]

            day_recipe_ids = list(
                {
                    recipe_id
                    for cell in slot_to_cell.values()
                    for recipe_id in cell.values()
                    if recipe_id is not None
                }
            )
            day_ingredients = ingredients[ingredients["recipe_id"].isin(day_recipe_ids)]
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

            # NB: a generated plan is a *draft* — it is not scheduled (no
            # meal_history) until the user explicitly confirms it. See
            # meal_planner.optimize.confirm.confirm_plan.

        update_plan_totals(
            conn,
            plan_run_id=plan_run_id,
            total_kcal=household_total_kcal,
            total_fiber=household_total_fiber,
        )

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
