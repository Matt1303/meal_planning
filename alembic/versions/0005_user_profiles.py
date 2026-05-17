"""add user_profile, plan_meal.profile_id, plan_day_profile

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.create_table(
        "user_profile",
        sa.Column("profile_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, unique=True, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("calories_daily_min", sa.Integer),
        sa.Column("calories_daily_max", sa.Integer),
        sa.Column("fiber_daily_min", sa.Integer),
        sa.Column("protein_daily_min", sa.Integer),
        sa.Column("protein_daily_max", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    # Seed sentinel id=0 = 'shared' so plan_meal.profile_id=0 satisfies the FK.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.user_profile (profile_id, name, display_name)
        VALUES (0, '__shared__', 'Shared')
        ON CONFLICT (profile_id) DO NOTHING
        """
    )
    # Advance the sequence past 0 so SERIAL generates >= 1 from now on.
    op.execute(
        f"SELECT setval(pg_get_serial_sequence('{SCHEMA}.user_profile', 'profile_id'), 1, false)"
    )

    op.add_column(
        "plan_meal",
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.user_profile.profile_id"),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    # Widen plan_meal PK so per-user slots can coexist with shared.
    # profile_id=0 means 'shared across all profiles in this plan_run'.
    op.execute(f"ALTER TABLE {SCHEMA}.plan_meal DROP CONSTRAINT IF EXISTS plan_meal_pkey")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.plan_meal
        ADD CONSTRAINT plan_meal_pkey PRIMARY KEY (plan_run_id, day, meal_type, profile_id)
        """
    )

    op.create_table(
        "plan_day_profile",
        sa.Column(
            "plan_run_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.user_profile.profile_id"),
            primary_key=True,
        ),
        sa.Column("kcal", sa.Numeric),
        sa.Column("fiber_g", sa.Numeric),
        sa.Column("protein_g", sa.Numeric),
        sa.Column("fat_g", sa.Numeric),
        sa.Column("carbs_g", sa.Numeric),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("plan_day_profile", schema=SCHEMA)
    op.execute(f"ALTER TABLE {SCHEMA}.plan_meal DROP CONSTRAINT IF EXISTS plan_meal_pkey")
    op.execute(f"DELETE FROM {SCHEMA}.plan_meal WHERE profile_id <> 0")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.plan_meal
        ADD CONSTRAINT plan_meal_pkey PRIMARY KEY (plan_run_id, day, meal_type)
        """
    )
    op.drop_column("plan_meal", "profile_id", schema=SCHEMA)
    op.drop_table("user_profile", schema=SCHEMA)
