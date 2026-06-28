"""confirm plans + shopping list

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.add_column(
        "plan_run",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "plan_run",
        sa.Column("scheduled_week", sa.Date, nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "shopping_list_item",
        sa.Column(
            "plan_run_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ingredient_canonical", sa.Text, primary_key=True),
        sa.Column("section", sa.Text, nullable=False),
        sa.Column("display_text", sa.Text, nullable=False),
        sa.Column("total_grams", sa.Numeric, nullable=True),
        sa.Column("checked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("shopping_list_item", schema=SCHEMA)
    op.drop_column("plan_run", "scheduled_week", schema=SCHEMA)
    op.drop_column("plan_run", "confirmed_at", schema=SCHEMA)
