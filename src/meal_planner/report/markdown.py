from __future__ import annotations

from datetime import UTC, datetime

from meal_planner.report.data import ReportData


def render_markdown(data: ReportData) -> str:
    lines: list[str] = []
    lines.append("# Weekly Plan Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if not data.plan_run.empty:
        row = data.plan_run.iloc[0]
        lines.append(
            f"Plan run: {int(row['plan_run_id'])} | "
            f"solver: {row['solver_status']} | "
            f"relaxation: {row['relaxation_level']} | "
            f"slack: {float(row['slack_total'] or 0):.2f}"
        )
    lines.append("")

    if not data.plan_meal.empty:
        for day in sorted(data.plan_meal["day"].unique()):
            lines.append(f"## Day {int(day)}")
            lines.append("")
            day_meals = data.plan_meal[data.plan_meal["day"] == day].sort_values("meal_type")
            for _, m in day_meals.iterrows():
                title = m["title"] or "(none)"
                lines.append(f"- {str(m['meal_type']).title()}: {title}")
            day_nutrition = data.plan_day[data.plan_day["day"] == day]
            if not day_nutrition.empty:
                kcal = float(day_nutrition.iloc[0]["kcal"] or 0)
                fiber = float(day_nutrition.iloc[0]["fiber_g"] or 0)
                lines.append(f"- Calories: {kcal:.0f} kcal")
                lines.append(f"- Fiber: {fiber:.1f} g")
            day_groups = data.plan_group[data.plan_group["day"] == day]
            if not day_groups.empty:
                lines.append("- Daily Dozen counts:")
                for _, g in day_groups.iterrows():
                    lines.append(
                        f"  - {g['food_group']}: {int(g['daily_count'])} "
                        f"({float(g['daily_portions'] or 0):.2f} portions)"
                    )
            lines.append("")

    lines.append("## Coverage")
    lines.append("")
    if not data.metrics.empty:
        for _, row in data.metrics.iterrows():
            lines.append(f"- {row['metric_name']}: {float(row['metric_value'] or 0):g}")
    return "\n".join(lines) + "\n"
