from datetime import datetime
import pandas as pd
from .db import get_engine


def build_report(plan_run_id, output_path="plan_report.md"):
    engine = get_engine()

    plan_meal = pd.read_sql(
        "SELECT day, meal_type, recipe_id FROM meal_planning.plan_meal WHERE plan_run_id = %s",
        engine,
        params=(plan_run_id,),
    )
    recipes = pd.read_sql("SELECT recipe_id, title FROM meal_planning.recipe", engine)
    plan_meal = plan_meal.merge(recipes, on="recipe_id", how="left")

    plan_day = pd.read_sql(
        "SELECT day, kcal, fiber_g FROM meal_planning.plan_day WHERE plan_run_id = %s",
        engine,
        params=(plan_run_id,),
    )

    groups = pd.read_sql(
        "SELECT day, food_group, daily_count, daily_portions FROM meal_planning.plan_day_group WHERE plan_run_id = %s",
        engine,
        params=(plan_run_id,),
    )

    lines = []
    lines.append(f"# Weekly Plan Report\n")
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    for day in sorted(plan_meal["day"].unique()):
        lines.append(f"## Day {day}\n")
        day_meals = plan_meal[plan_meal["day"] == day].sort_values("meal_type")
        for _, row in day_meals.iterrows():
            lines.append(f"- {row['meal_type'].title()}: {row['title'] or 'None'}")
        day_nut = plan_day[plan_day["day"] == day]
        if not day_nut.empty:
            kcal = float(day_nut["kcal"].iloc[0] or 0.0)
            fiber = float(day_nut["fiber_g"].iloc[0] or 0.0)
            lines.append(f"- Calories: {kcal:.0f} kcal")
            lines.append(f"- Fiber: {fiber:.1f} g")

        day_groups = groups[groups["day"] == day]
        if not day_groups.empty:
            lines.append("- Daily Dozen counts:")
            for _, g in day_groups.iterrows():
                lines.append(f"  - {g['food_group']}: {int(g['daily_count'])} ({float(g['daily_portions']):.2f} portions)")
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    return output_path
