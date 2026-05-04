from __future__ import annotations

import base64
from pathlib import Path

from meal_planner.report.data import ReportData


def render_html(data: ReportData, image_paths: list[Path]) -> str:
    rows: list[str] = []
    if not data.plan_meal.empty:
        meal_order = ["breakfast", "lunch", "dinner", "snack"]
        pivot = data.plan_meal.pivot(index="day", columns="meal_type", values="title").reindex(
            columns=meal_order
        )
        rows.append("<table><thead><tr><th>Day</th>")
        for meal in meal_order:
            rows.append(f"<th>{meal.title()}</th>")
        rows.append("</tr></thead><tbody>")
        for day, row in pivot.iterrows():
            rows.append(f"<tr><td>{int(day)}</td>")
            for meal in meal_order:
                val = row.get(meal) or ""
                rows.append(f"<td>{val}</td>")
            rows.append("</tr>")
        rows.append("</tbody></table>")

    images = "".join(_embed(p) for p in image_paths if p.exists())
    metrics_block = ""
    if not data.metrics.empty:
        items = "".join(
            f"<li>{row['metric_name']}: {float(row['metric_value'] or 0):g}</li>"
            for _, row in data.metrics.iterrows()
        )
        metrics_block = f"<h2>Coverage</h2><ul>{items}</ul>"

    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Meal plan</title>"
        "<style>"
        "body{font-family:Helvetica,Arial,sans-serif;margin:24px;}"
        "table{border-collapse:collapse;}td,th{border:1px solid #ccc;padding:6px 10px;}"
        "img{max-width:100%;height:auto;margin:12px 0;}"
        "</style></head><body>"
        "<h1>Meal Plan Report</h1>"
        f"{''.join(rows)}"
        f"{images}"
        f"{metrics_block}"
        "</body></html>"
    )


def _embed(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"<img src='data:image/png;base64,{encoded}' alt='{path.name}'/>"
