from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class PlanRunRow:
    plan_run_id: int
    run_time: datetime
    status: str | None
    solver_status: str | None
    total_kcal: Decimal | None
    total_fiber: Decimal | None
    solver_seconds: Decimal | None
    slack_total: Decimal | None
    relaxation_level: int | None
    correlation_id: str | None
    config_id: int | None


def insert_plan_config(
    conn: Connection,
    *,
    name: str,
    payload: dict[str, Any],
    optimizer: dict[str, Any],
) -> int:
    payload_json = json.dumps(payload, default=_json_default)
    row = conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_config
                (name, payload, min_rating, rating_weight, recency_half_life_days,
                 calories_min, calories_max, fiber_min, snack_optional)
            VALUES (:name, :payload, :min_rating, :rating_weight, :half_life,
                    :cal_min, :cal_max, :fiber_min, :snack_optional)
            RETURNING config_id
            """
        ),
        {
            "name": name,
            "payload": payload_json,
            "min_rating": optimizer.get("min_rating"),
            "rating_weight": optimizer.get("rating_weight"),
            "half_life": optimizer.get("recency_half_life_days"),
            "cal_min": optimizer.get("calories_daily_min"),
            "cal_max": optimizer.get("calories_daily_max"),
            "fiber_min": optimizer.get("fiber_daily_min"),
            "snack_optional": optimizer.get("snack_optional"),
        },
    ).scalar_one()
    return int(row)


def insert_plan_run(
    conn: Connection,
    *,
    run_time: datetime,
    config_id: int,
    status: str,
    solver_status: str | None,
    solver_seconds: Decimal | float | None,
    slack_total: Decimal | float | None,
    relaxation_level: int,
    correlation_id: str,
) -> int:
    row = conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_run
                (run_time, config_id, status, solver_status,
                 solver_seconds, slack_total, relaxation_level, correlation_id)
            VALUES (:rt, :cid, :status, :ss, :secs, :slack, :rlx, :corr)
            RETURNING plan_run_id
            """
        ),
        {
            "rt": run_time,
            "cid": config_id,
            "status": status,
            "ss": solver_status,
            "secs": Decimal(str(solver_seconds)) if solver_seconds is not None else None,
            "slack": Decimal(str(slack_total)) if slack_total is not None else None,
            "rlx": relaxation_level,
            "corr": correlation_id,
        },
    ).scalar_one()
    return int(row)


def insert_plan_meal(
    conn: Connection,
    *,
    plan_run_id: int,
    day: int,
    meal_type: str,
    recipe_id: int | None,
    profile_id: int = 0,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_meal
                (plan_run_id, day, meal_type, profile_id, recipe_id)
            VALUES (:pr, :d, :m, :pid, :r)
            """
        ),
        {"pr": plan_run_id, "d": day, "m": meal_type, "pid": profile_id, "r": recipe_id},
    )


def insert_plan_day_profile(
    conn: Connection,
    *,
    plan_run_id: int,
    day: int,
    profile_id: int,
    kcal: Decimal | float,
    fiber_g: Decimal | float,
    protein_g: Decimal | float,
    fat_g: Decimal | float,
    carbs_g: Decimal | float,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_day_profile
                (plan_run_id, day, profile_id, kcal, fiber_g, protein_g, fat_g, carbs_g)
            VALUES (:pr, :d, :pid, :k, :f, :p, :ft, :c)
            """
        ),
        {
            "pr": plan_run_id,
            "d": day,
            "pid": profile_id,
            "k": Decimal(str(kcal)),
            "f": Decimal(str(fiber_g)),
            "p": Decimal(str(protein_g)),
            "ft": Decimal(str(fat_g)),
            "c": Decimal(str(carbs_g)),
        },
    )


def insert_plan_day(
    conn: Connection,
    *,
    plan_run_id: int,
    day: int,
    kcal: Decimal | float,
    fiber_g: Decimal | float,
    protein_g: Decimal | float | None = None,
    fat_g: Decimal | float | None = None,
    carbs_g: Decimal | float | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_day
                (plan_run_id, day, kcal, fiber_g, protein_g, fat_g, carbs_g)
            VALUES (:pr, :d, :k, :f, :p, :ft, :c)
            """
        ),
        {
            "pr": plan_run_id,
            "d": day,
            "k": Decimal(str(kcal)),
            "f": Decimal(str(fiber_g)),
            "p": Decimal(str(protein_g)) if protein_g is not None else None,
            "ft": Decimal(str(fat_g)) if fat_g is not None else None,
            "c": Decimal(str(carbs_g)) if carbs_g is not None else None,
        },
    )


def insert_plan_day_group(
    conn: Connection,
    *,
    plan_run_id: int,
    day: int,
    food_group: str,
    daily_count: int,
    daily_portions: Decimal | float,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.plan_day_group
                (plan_run_id, day, food_group, daily_count, daily_portions)
            VALUES (:pr, :d, :fg, :c, :p)
            """
        ),
        {
            "pr": plan_run_id,
            "d": day,
            "fg": food_group,
            "c": daily_count,
            "p": Decimal(str(daily_portions)),
        },
    )


def insert_meal_history(
    conn: Connection, *, recipe_id: int, meal_type: str, planned_for: date
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO meal_planning.meal_history (recipe_id, meal_type, planned_for)
            VALUES (:r, :m, :d) ON CONFLICT DO NOTHING
            """
        ),
        {"r": recipe_id, "m": meal_type, "d": planned_for},
    )


def update_plan_totals(
    conn: Connection, *, plan_run_id: int, total_kcal: Decimal, total_fiber: Decimal
) -> None:
    conn.execute(
        text(
            """
            UPDATE meal_planning.plan_run
            SET total_kcal = :k, total_fiber = :f
            WHERE plan_run_id = :pr
            """
        ),
        {"k": total_kcal, "f": total_fiber, "pr": plan_run_id},
    )


def fetch_plan_run(conn: Connection, plan_run_id: int) -> PlanRunRow | None:
    row = conn.execute(
        text(
            """
            SELECT plan_run_id, run_time, status, solver_status, total_kcal, total_fiber,
                   solver_seconds, slack_total, relaxation_level, correlation_id, config_id
            FROM meal_planning.plan_run WHERE plan_run_id = :pr
            """
        ),
        {"pr": plan_run_id},
    ).fetchone()
    if row is None:
        return None
    return PlanRunRow(
        plan_run_id=int(row[0]),
        run_time=row[1],
        status=row[2],
        solver_status=row[3],
        total_kcal=row[4],
        total_fiber=row[5],
        solver_seconds=row[6],
        slack_total=row[7],
        relaxation_level=row[8],
        correlation_id=row[9],
        config_id=row[10],
    )


def latest_plan_run_id(conn: Connection) -> int | None:
    row = conn.execute(
        text("SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1")
    ).fetchone()
    return int(row[0]) if row else None


def _json_default(obj: object) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)
