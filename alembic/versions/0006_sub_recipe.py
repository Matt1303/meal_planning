"""add recipe_ingredient.sub_recipe_id for (separate recipe) markers

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.add_column(
        "recipe_ingredient",
        sa.Column("sub_recipe_id", sa.Integer, nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "recipe_ingredient_sub_recipe_id_fkey",
        "recipe_ingredient",
        "recipe",
        ["sub_recipe_id"],
        ["recipe_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_recipe_ingredient_sub_recipe_id",
        "recipe_ingredient",
        ["sub_recipe_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_recipe_ingredient_sub_recipe_id",
        table_name="recipe_ingredient",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "recipe_ingredient_sub_recipe_id_fkey",
        "recipe_ingredient",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("recipe_ingredient", "sub_recipe_id", schema=SCHEMA)
