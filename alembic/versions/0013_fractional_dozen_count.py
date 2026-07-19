"""daily dozen counts are fractional

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.alter_column(
        "plan_day_group",
        "daily_count",
        type_=sa.Numeric(),
        existing_type=sa.Integer(),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.alter_column(
        "plan_day_group",
        "daily_count",
        type_=sa.Integer(),
        existing_type=sa.Numeric(),
        schema=SCHEMA,
    )
