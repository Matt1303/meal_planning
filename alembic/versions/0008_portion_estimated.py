"""add recipe_ingredient.portion_estimated

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.add_column(
        "recipe_ingredient",
        sa.Column("portion_estimated", sa.Boolean, nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("recipe_ingredient", "portion_estimated", schema=SCHEMA)
