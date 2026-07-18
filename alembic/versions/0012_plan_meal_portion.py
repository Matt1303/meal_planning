"""per-person servings of a shared dish

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.create_table(
        "plan_meal_portion",
        sa.Column(
            "plan_run_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Integer(), primary_key=True),
        sa.Column("meal_type", sa.Text(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.user_profile.profile_id"),
            primary_key=True,
        ),
        sa.Column("servings", sa.Numeric(), nullable=False, server_default="1"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("plan_meal_portion", schema=SCHEMA)
