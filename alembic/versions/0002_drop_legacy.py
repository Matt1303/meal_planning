"""drop legacy tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.weekly_meal_plan CASCADE")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.processed_recipes CASCADE")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.recipes CASCADE")


def downgrade() -> None:
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text, unique=True, nullable=False),
        sa.Column("ingredients", sa.Text),
        sa.Column("categories", sa.Text),
        sa.Column("rating", sa.Integer),
        sa.Column("servings", sa.Text),
        sa.Column("difficulty", sa.Text),
        sa.Column("lastmodifieddate", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_table(
        "processed_recipes",
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("ingredient", sa.Text, nullable=False),
        sa.Column("serving_quantity", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("breakfasts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lunches", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dinner", sa.Integer, nullable=False, server_default="0"),
        sa.Column("snacks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lastmodifieddate", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("title", "ingredient"),
        schema=SCHEMA,
    )
    op.create_table(
        "weekly_meal_plan",
        sa.Column("run_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("week_number", sa.Integer, nullable=False),
        sa.Column("day", sa.Integer, nullable=False),
        sa.Column("breakfast", sa.Text),
        sa.Column("lunch", sa.Text),
        sa.Column("dinner", sa.Text),
        sa.Column("snack", sa.Text),
        schema=SCHEMA,
    )
