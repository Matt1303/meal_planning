from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text

from meal_planner.config import ProfileTargets


@dataclass(frozen=True)
class ProfileRecord:
    profile_id: int
    name: str
    display_name: str | None
    calories_daily_min: int | None
    calories_daily_max: int | None
    fiber_daily_min: int | None
    protein_daily_min: int | None
    protein_daily_max: int | None


def upsert_profile(conn: Connection, profile: ProfileTargets) -> int:
    row = conn.execute(
        text(
            """
            INSERT INTO meal_planning.user_profile
                (name, display_name, calories_daily_min, calories_daily_max,
                 fiber_daily_min, protein_daily_min, protein_daily_max)
            VALUES (:name, :display_name, :cal_min, :cal_max, :fib_min, :pro_min, :pro_max)
            ON CONFLICT (name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                calories_daily_min = EXCLUDED.calories_daily_min,
                calories_daily_max = EXCLUDED.calories_daily_max,
                fiber_daily_min = EXCLUDED.fiber_daily_min,
                protein_daily_min = EXCLUDED.protein_daily_min,
                protein_daily_max = EXCLUDED.protein_daily_max
            RETURNING profile_id
            """
        ),
        {
            "name": profile.name,
            "display_name": profile.display_name,
            "cal_min": profile.calories_daily_min,
            "cal_max": profile.calories_daily_max,
            "fib_min": profile.fiber_daily_min,
            "pro_min": profile.protein_daily_min,
            "pro_max": profile.protein_daily_max,
        },
    ).scalar_one()
    return int(row)


def list_profiles(conn: Connection) -> list[ProfileRecord]:
    rows = conn.execute(
        text(
            """
            SELECT profile_id, name, display_name,
                   calories_daily_min, calories_daily_max,
                   fiber_daily_min, protein_daily_min, protein_daily_max
            FROM meal_planning.user_profile
            ORDER BY profile_id
            """
        )
    ).fetchall()
    return [
        ProfileRecord(
            profile_id=int(r[0]),
            name=str(r[1]),
            display_name=r[2],
            calories_daily_min=r[3],
            calories_daily_max=r[4],
            fiber_daily_min=r[5],
            protein_daily_min=r[6],
            protein_daily_max=r[7],
        )
        for r in rows
    ]


def fetch_profile_by_name(conn: Connection, name: str) -> ProfileRecord | None:
    row = conn.execute(
        text(
            """
            SELECT profile_id, name, display_name,
                   calories_daily_min, calories_daily_max,
                   fiber_daily_min, protein_daily_min, protein_daily_max
            FROM meal_planning.user_profile
            WHERE name = :name
            """
        ),
        {"name": name},
    ).fetchone()
    if row is None:
        return None
    return ProfileRecord(
        profile_id=int(row[0]),
        name=str(row[1]),
        display_name=row[2],
        calories_daily_min=row[3],
        calories_daily_max=row[4],
        fiber_daily_min=row[5],
        protein_daily_min=row[6],
        protein_daily_max=row[7],
    )
