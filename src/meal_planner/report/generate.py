from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from meal_planner.db import get_engine
from meal_planner.report.charts import save_group_heatmap, save_kcal_fiber, save_top_ingredients
from meal_planner.report.data import ReportData, load_report_data


@dataclass(frozen=True)
class ReportFormats:
    md: bool = True
    docx: bool = False
    html: bool = False


def generate_report(
    plan_run_id: int,
    *,
    engine: Engine | None = None,
    output_dir: Path = Path("reports"),
    formats: ReportFormats = ReportFormats(),
    data: ReportData | None = None,
) -> dict[str, Path]:
    eng = engine or get_engine()
    report_data = data or load_report_data(plan_run_id, engine=eng)
    output_dir.mkdir(parents=True, exist_ok=True)

    images: list[Path] = []
    if not report_data.plan_day.empty:
        images.append(
            save_kcal_fiber(report_data, output_dir / f"plan_{plan_run_id}_kcal_fiber.png")
        )
    if not report_data.plan_group.empty:
        images.append(
            save_group_heatmap(report_data, output_dir / f"plan_{plan_run_id}_daily_dozen.png")
        )
    if not report_data.plan_meal.empty:
        ingredients = _fetch_top_ingredients(eng, report_data)
        if not ingredients.empty:
            top_path = save_top_ingredients(
                ingredients, output_dir / f"plan_{plan_run_id}_top_ingredients.png"
            )
            if top_path is not None:
                images.append(top_path)

    outputs: dict[str, Path] = {}

    if formats.md:
        from meal_planner.report.markdown import render_markdown

        md_path = output_dir / f"plan_{plan_run_id}.md"
        md_path.write_text(render_markdown(report_data))
        outputs["md"] = md_path
        legacy = Path("plan_report.md")
        legacy.write_text(render_markdown(report_data))
        outputs["legacy_md"] = legacy

    if formats.html:
        from meal_planner.report.html import render_html

        html_path = output_dir / f"plan_{plan_run_id}.html"
        html_path.write_text(render_html(report_data, images))
        outputs["html"] = html_path

    if formats.docx:
        from meal_planner.report.docx_writer import write_docx

        docx_path = output_dir / f"plan_{plan_run_id}.docx"
        write_docx(report_data, images, docx_path)
        outputs["docx"] = docx_path

    return outputs


def _fetch_top_ingredients(engine: Engine, data: ReportData) -> pd.Series:
    if data.plan_meal.empty:
        return pd.Series(dtype=str)
    recipe_ids: Iterable[int] = [
        int(r) for r in data.plan_meal["recipe_id"].dropna().unique().tolist()
    ]
    if not list(recipe_ids):
        return pd.Series(dtype=str)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ingredient_canonical
                FROM meal_planning.recipe_ingredient
                WHERE recipe_id = ANY(:recipe_ids) AND ingredient_canonical IS NOT NULL
                """
            ),
            {"recipe_ids": list(recipe_ids)},
        ).fetchall()
    return pd.Series([str(r[0]) for r in rows if r[0] is not None])
