from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Connection, Engine, text

from meal_planner.config import Settings
from meal_planner.db import get_engine
from meal_planner.logging import get_logger
from meal_planner.shopping import build_shopping_list

log = get_logger(__name__)


@dataclass(frozen=True)
class PlanStatus:
    plan_run_id: int
    confirmed: bool
    scheduled_week: date | None


def plan_status(conn: Connection, plan_run_id: int) -> PlanStatus:
    row = conn.execute(
        text(
            "SELECT confirmed_at, scheduled_week FROM meal_planning.plan_run "
            "WHERE plan_run_id = :pr"
        ),
        {"pr": plan_run_id},
    ).fetchone()
    if row is None:
        return PlanStatus(plan_run_id, False, None)
    return PlanStatus(plan_run_id, row[0] is not None, row[1])


def confirm_plan(
    plan_run_id: int,
    week_start: date,
    settings: Settings,
    *,
    engine: Engine | None = None,
) -> int:
    """Lock in a draft plan: mark it confirmed, schedule its meals (meal_history,
    so 'last scheduled' tracking only reflects confirmed plans), and snapshot a
    shopping list. Returns the number of shopping-list items."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                "UPDATE meal_planning.plan_run "
                "SET confirmed_at = now(), scheduled_week = :wk, status = 'confirmed' "
                "WHERE plan_run_id = :pr"
            ),
            {"wk": week_start, "pr": plan_run_id},
        )

        meals = conn.execute(
            text(
                "SELECT day, meal_type, recipe_id FROM meal_planning.plan_meal "
                "WHERE plan_run_id = :pr AND recipe_id IS NOT NULL"
            ),
            {"pr": plan_run_id},
        ).fetchall()
        for day, meal_type, recipe_id in meals:
            planned_for = week_start + timedelta(days=int(day) - 1)
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.meal_history (recipe_id, meal_type, planned_for)
                    VALUES (:rid, :mt, :pf)
                    ON CONFLICT (recipe_id, meal_type, planned_for) DO NOTHING
                    """
                ),
                {"rid": int(recipe_id), "mt": str(meal_type), "pf": planned_for},
            )

        items = build_shopping_list(conn, plan_run_id, settings)
        conn.execute(
            text("DELETE FROM meal_planning.shopping_list_item WHERE plan_run_id = :pr"),
            {"pr": plan_run_id},
        )
        for item in items:
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.shopping_list_item
                        (plan_run_id, ingredient_canonical, section, display_text,
                         total_grams, checked, sort_order)
                    VALUES (:pr, :ic, :sec, :disp, :grams, FALSE, :ord)
                    """
                ),
                {
                    "pr": plan_run_id,
                    "ic": item.ingredient_canonical,
                    "sec": item.section,
                    "disp": item.display_text,
                    "grams": item.total_grams,
                    "ord": item.sort_order,
                },
            )

    log.info(
        "plan.confirmed",
        plan_run_id=plan_run_id,
        scheduled_week=str(week_start),
        shopping_items=len(items),
    )
    return len(items)
