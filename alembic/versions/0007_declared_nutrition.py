"""add declared (Paprika Nutrition section) per-serving macros to recipe

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"
_COLS = (
    "declared_kcal",
    "declared_protein_g",
    "declared_fiber_g",
    "declared_fat_g",
    "declared_carbs_g",
)


def upgrade() -> None:
    for col in _COLS:
        op.add_column("recipe", sa.Column(col, sa.Numeric, nullable=True), schema=SCHEMA)


def downgrade() -> None:
    for col in _COLS:
        op.drop_column("recipe", col, schema=SCHEMA)
