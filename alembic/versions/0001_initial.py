"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

SCHEMA = "meal_planning"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "recipe",
        sa.Column("recipe_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text, unique=True, nullable=False),
        sa.Column("rating", sa.Numeric),
        sa.Column("servings", sa.Text),
        sa.Column("servings_count", sa.Numeric),
        sa.Column("difficulty", sa.Text),
        sa.Column("categories", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("last_modified", sa.DateTime(timezone=True)),
        sa.Column("is_plant_based", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        schema=SCHEMA,
    )

    op.create_table(
        "recipe_source",
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_path", sa.Text, primary_key=True),
        sa.Column("raw_html", sa.Text),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "recipe_meal_type",
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("meal_type", sa.Text, primary_key=True),
        schema=SCHEMA,
    )
    op.create_index("idx_recipe_meal_type_meal", "recipe_meal_type", ["meal_type"], schema=SCHEMA)

    op.create_table(
        "recipe_ingredient",
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("raw_text", sa.Text, primary_key=True),
        sa.Column("ingredient_name", sa.Text),
        sa.Column("ingredient_canonical", sa.Text),
        sa.Column("quantity_value", sa.Numeric),
        sa.Column("quantity_unit", sa.Text),
        sa.Column("quantity_grams", sa.Numeric),
        sa.Column("per_serving_grams", sa.Numeric),
        sa.Column("food_group", sa.Text),
        sa.Column("portions", sa.Numeric),
        sa.Column("portion_met", sa.Boolean),
        schema=SCHEMA,
    )
    op.create_index("idx_recipe_ingredient_recipe_id", "recipe_ingredient", ["recipe_id"], schema=SCHEMA)
    op.create_index("idx_recipe_ingredient_canonical", "recipe_ingredient", ["ingredient_canonical"], schema=SCHEMA)
    op.create_index("idx_recipe_ingredient_food_group", "recipe_ingredient", ["food_group"], schema=SCHEMA)

    op.create_table(
        "ingredient_nutrition_cache",
        sa.Column("ingredient_canonical", sa.Text, primary_key=True),
        sa.Column("kcal_per_100g", sa.Numeric),
        sa.Column("fiber_g_per_100g", sa.Numeric),
        sa.Column("protein_g_per_100g", sa.Numeric),
        sa.Column("fat_g_per_100g", sa.Numeric),
        sa.Column("carbs_g_per_100g", sa.Numeric),
        sa.Column("source", sa.Text),
        sa.Column("match_score", sa.Numeric),
        sa.Column("match_source_name", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "ingredient_parse_cache",
        sa.Column("raw_text", sa.Text, primary_key=True),
        sa.Column("ingredient_name", sa.Text),
        sa.Column("ingredient_canonical", sa.Text),
        sa.Column("quantity_value", sa.Numeric),
        sa.Column("quantity_unit", sa.Text),
        sa.Column("quantity_grams", sa.Numeric),
        sa.Column("food_group", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "ingredient_override",
        sa.Column("raw_text", sa.Text, primary_key=True),
        sa.Column("ingredient_canonical", sa.Text),
        sa.Column("food_group", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "recipe_nutrition",
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("calories_kcal", sa.Numeric),
        sa.Column("fiber_g", sa.Numeric),
        sa.Column("per_serving_kcal", sa.Numeric),
        sa.Column("per_serving_fiber_g", sa.Numeric),
        sa.Column("protein_g", sa.Numeric),
        sa.Column("fat_g", sa.Numeric),
        sa.Column("carbs_g", sa.Numeric),
        schema=SCHEMA,
    )

    op.create_table(
        "meal_history",
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("meal_type", sa.Text, primary_key=True),
        sa.Column("planned_for", sa.Date, primary_key=True),
        schema=SCHEMA,
    )
    op.create_index("idx_meal_history_planned_for", "meal_history", ["planned_for"], schema=SCHEMA)

    op.create_table(
        "plan_config",
        sa.Column("config_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text),
        sa.Column("payload", sa.Text),
        sa.Column("min_rating", sa.Numeric),
        sa.Column("rating_weight", sa.Numeric),
        sa.Column("recency_half_life_days", sa.Integer),
        sa.Column("calories_min", sa.Integer),
        sa.Column("calories_max", sa.Integer),
        sa.Column("fiber_min", sa.Integer),
        sa.Column("snack_optional", sa.Boolean),
        schema=SCHEMA,
    )

    op.create_table(
        "plan_run",
        sa.Column("plan_run_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.plan_config.config_id")),
        sa.Column("status", sa.Text),
        sa.Column("solver_status", sa.Text),
        sa.Column("total_kcal", sa.Numeric),
        sa.Column("total_fiber", sa.Numeric),
        sa.Column("solver_seconds", sa.Numeric),
        sa.Column("slack_total", sa.Numeric),
        sa.Column("relaxation_level", sa.Integer),
        sa.Column("correlation_id", sa.Text),
        schema=SCHEMA,
    )

    op.create_table(
        "plan_meal",
        sa.Column("plan_run_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Integer, primary_key=True),
        sa.Column("meal_type", sa.Text, primary_key=True),
        sa.Column("recipe_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.recipe.recipe_id")),
        schema=SCHEMA,
    )

    op.create_table(
        "plan_day",
        sa.Column("plan_run_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Integer, primary_key=True),
        sa.Column("kcal", sa.Numeric),
        sa.Column("fiber_g", sa.Numeric),
        schema=SCHEMA,
    )

    op.create_table(
        "plan_day_group",
        sa.Column("plan_run_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Integer, primary_key=True),
        sa.Column("food_group", sa.Text, primary_key=True),
        sa.Column("daily_count", sa.Integer),
        sa.Column("daily_portions", sa.Numeric),
        schema=SCHEMA,
    )

    op.create_table(
        "pipeline_metric",
        sa.Column("metric_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("metric_time", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("metric_name", sa.Text, nullable=False),
        sa.Column("metric_value", sa.Numeric),
        sa.Column("plan_run_id", sa.Integer),
        sa.Column("correlation_id", sa.Text),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in [
        "pipeline_metric",
        "plan_day_group",
        "plan_day",
        "plan_meal",
        "plan_run",
        "plan_config",
        "meal_history",
        "recipe_nutrition",
        "ingredient_override",
        "ingredient_parse_cache",
        "ingredient_nutrition_cache",
        "recipe_ingredient",
        "recipe_meal_type",
        "recipe_source",
        "recipe",
    ]:
        op.drop_table(table, schema=SCHEMA)
