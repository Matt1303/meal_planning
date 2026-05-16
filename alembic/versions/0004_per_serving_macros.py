"""add per-serving protein/fat/carbs columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    for col in ("per_serving_protein_g", "per_serving_fat_g", "per_serving_carbs_g"):
        op.add_column("recipe_nutrition", sa.Column(col, sa.Numeric), schema=SCHEMA)
    op.execute(
        f"""
        UPDATE {SCHEMA}.recipe_nutrition rn
        SET per_serving_protein_g = rn.protein_g,
            per_serving_fat_g = rn.fat_g,
            per_serving_carbs_g = rn.carbs_g
        WHERE rn.per_serving_protein_g IS NULL
        """
    )

    for col in ("protein_g", "fat_g", "carbs_g"):
        op.add_column("plan_day", sa.Column(col, sa.Numeric), schema=SCHEMA)


def downgrade() -> None:
    for col in ("protein_g", "fat_g", "carbs_g"):
        op.drop_column("plan_day", col, schema=SCHEMA)
    for col in ("per_serving_protein_g", "per_serving_fat_g", "per_serving_carbs_g"):
        op.drop_column("recipe_nutrition", col, schema=SCHEMA)
