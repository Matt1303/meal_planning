"""per-person whey scoops allocated by the solver

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.add_column(
        "plan_day_profile",
        sa.Column("whey_scoops", sa.Integer, nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("plan_day_profile", "whey_scoops", schema=SCHEMA)
