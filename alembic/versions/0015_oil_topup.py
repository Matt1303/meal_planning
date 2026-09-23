"""record the olive oil the solver allocates for fat

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    # Mirrors whey_scoops: a flexible top-up the optimiser allocates, so the
    # stored day has to say how much of it the totals include.
    op.add_column(
        "plan_day_profile",
        sa.Column("oil_grams", sa.Numeric(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("plan_day_profile", "oil_grams", schema=SCHEMA)
