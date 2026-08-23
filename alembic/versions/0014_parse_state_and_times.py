"""record parse state explicitly, and recipe prep/cook minutes

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    # "Has this line been through parse?" was inferred from NULL columns, but a
    # NULL food group is the *correct* settled answer for most lines (olive oil
    # is not a Daily Dozen food), so 930 of 2,285 rows re-parsed every run —
    # including 52 LLM calls per refresh for lines that will never resolve.
    op.add_column(
        "recipe_ingredient",
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # Every existing row has been through parse at least once.
    op.execute(f"UPDATE {SCHEMA}.recipe_ingredient SET parsed_at = now()")

    # Paprika exports carry prepTime/cookTime; captured for the coming
    # kitchen-time budget.
    op.add_column("recipe", sa.Column("prep_minutes", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("recipe", sa.Column("cook_minutes", sa.Integer(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("recipe_ingredient", "parsed_at", schema=SCHEMA)
    op.drop_column("recipe", "prep_minutes", schema=SCHEMA)
    op.drop_column("recipe", "cook_minutes", schema=SCHEMA)
