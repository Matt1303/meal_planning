"""allow fractional whey scoops

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.alter_column(
        "plan_day_profile",
        "whey_scoops",
        type_=sa.Numeric(),
        existing_type=sa.Integer(),
        existing_nullable=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.alter_column(
        "plan_day_profile",
        "whey_scoops",
        type_=sa.Integer(),
        existing_type=sa.Numeric(),
        existing_nullable=False,
        schema=SCHEMA,
    )
