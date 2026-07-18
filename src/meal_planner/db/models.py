from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "meal_planning"


class Base(DeclarativeBase):
    pass


class Recipe(Base):
    __tablename__ = "recipe"
    __table_args__ = {"schema": SCHEMA}

    recipe_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    rating: Mapped[Decimal | None] = mapped_column(Numeric)
    servings: Mapped[str | None] = mapped_column(Text)
    servings_count: Mapped[Decimal | None] = mapped_column(Numeric)
    difficulty: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_plant_based: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    declared_kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    declared_protein_g: Mapped[Decimal | None] = mapped_column(Numeric)
    declared_fiber_g: Mapped[Decimal | None] = mapped_column(Numeric)
    declared_fat_g: Mapped[Decimal | None] = mapped_column(Numeric)
    declared_carbs_g: Mapped[Decimal | None] = mapped_column(Numeric)


class RecipeSource(Base):
    __tablename__ = "recipe_source"
    __table_args__ = {"schema": SCHEMA}

    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_path: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_html: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecipeMealType(Base):
    __tablename__ = "recipe_meal_type"
    __table_args__ = {"schema": SCHEMA}

    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    meal_type: Mapped[str] = mapped_column(Text, primary_key=True)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredient"
    __table_args__ = (
        Index("idx_recipe_ingredient_recipe_id", "recipe_id"),
        Index("idx_recipe_ingredient_canonical", "ingredient_canonical"),
        Index("idx_recipe_ingredient_food_group", "food_group"),
        {"schema": SCHEMA},
    )

    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    raw_text: Mapped[str] = mapped_column(Text, primary_key=True)
    ingredient_name: Mapped[str | None] = mapped_column(Text)
    ingredient_canonical: Mapped[str | None] = mapped_column(Text)
    quantity_value: Mapped[Decimal | None] = mapped_column(Numeric)
    quantity_unit: Mapped[str | None] = mapped_column(Text)
    quantity_grams: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_grams: Mapped[Decimal | None] = mapped_column(Numeric)
    food_group: Mapped[str | None] = mapped_column(Text)
    portions: Mapped[Decimal | None] = mapped_column(Numeric)
    portion_met: Mapped[bool | None] = mapped_column(Boolean)
    portion_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sub_recipe_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="SET NULL"),
    )


class IngredientNutritionCache(Base):
    __tablename__ = "ingredient_nutrition_cache"
    __table_args__ = {"schema": SCHEMA}

    ingredient_canonical: Mapped[str] = mapped_column(Text, primary_key=True)
    kcal_per_100g: Mapped[Decimal | None] = mapped_column(Numeric)
    fiber_g_per_100g: Mapped[Decimal | None] = mapped_column(Numeric)
    protein_g_per_100g: Mapped[Decimal | None] = mapped_column(Numeric)
    fat_g_per_100g: Mapped[Decimal | None] = mapped_column(Numeric)
    carbs_g_per_100g: Mapped[Decimal | None] = mapped_column(Numeric)
    source: Mapped[str | None] = mapped_column(Text)
    match_score: Mapped[Decimal | None] = mapped_column(Numeric)
    match_source_name: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngredientParseCache(Base):
    __tablename__ = "ingredient_parse_cache"
    __table_args__ = {"schema": SCHEMA}

    raw_text: Mapped[str] = mapped_column(Text, primary_key=True)
    ingredient_name: Mapped[str | None] = mapped_column(Text)
    ingredient_canonical: Mapped[str | None] = mapped_column(Text)
    quantity_value: Mapped[Decimal | None] = mapped_column(Numeric)
    quantity_unit: Mapped[str | None] = mapped_column(Text)
    quantity_grams: Mapped[Decimal | None] = mapped_column(Numeric)
    food_group: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngredientOverride(Base):
    __tablename__ = "ingredient_override"
    __table_args__ = {"schema": SCHEMA}

    raw_text: Mapped[str] = mapped_column(Text, primary_key=True)
    ingredient_canonical: Mapped[str | None] = mapped_column(Text)
    food_group: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecipeNutrition(Base):
    __tablename__ = "recipe_nutrition"
    __table_args__ = {"schema": SCHEMA}

    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    calories_kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_fiber_g: Mapped[Decimal | None] = mapped_column(Numeric)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric)
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_protein_g: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_fat_g: Mapped[Decimal | None] = mapped_column(Numeric)
    per_serving_carbs_g: Mapped[Decimal | None] = mapped_column(Numeric)


class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = {"schema": SCHEMA}

    profile_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    calories_daily_min: Mapped[int | None] = mapped_column(Integer)
    calories_daily_max: Mapped[int | None] = mapped_column(Integer)
    fiber_daily_min: Mapped[int | None] = mapped_column(Integer)
    protein_daily_min: Mapped[int | None] = mapped_column(Integer)
    protein_daily_max: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MealHistory(Base):
    __tablename__ = "meal_history"
    __table_args__ = (
        Index("idx_meal_history_planned_for", "planned_for"),
        {"schema": SCHEMA},
    )

    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    meal_type: Mapped[str] = mapped_column(Text, primary_key=True)
    planned_for: Mapped[date] = mapped_column(Date, primary_key=True)


class PlanConfig(Base):
    __tablename__ = "plan_config"
    __table_args__ = {"schema": SCHEMA}

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(Text)
    min_rating: Mapped[Decimal | None] = mapped_column(Numeric)
    rating_weight: Mapped[Decimal | None] = mapped_column(Numeric)
    recency_half_life_days: Mapped[int | None] = mapped_column(Integer)
    calories_min: Mapped[int | None] = mapped_column(Integer)
    calories_max: Mapped[int | None] = mapped_column(Integer)
    fiber_min: Mapped[int | None] = mapped_column(Integer)
    snack_optional: Mapped[bool | None] = mapped_column(Boolean)


class PlanRun(Base):
    __tablename__ = "plan_run"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    config_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_config.config_id"),
    )
    status: Mapped[str | None] = mapped_column(Text)
    solver_status: Mapped[str | None] = mapped_column(Text)
    total_kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    total_fiber: Mapped[Decimal | None] = mapped_column(Numeric)
    solver_seconds: Mapped[Decimal | None] = mapped_column(Numeric)
    slack_total: Mapped[Decimal | None] = mapped_column(Numeric)
    relaxation_level: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_week: Mapped[date | None] = mapped_column(Date)


class ShoppingListItem(Base):
    __tablename__ = "shopping_list_item"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ingredient_canonical: Mapped[str] = mapped_column(Text, primary_key=True)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    total_grams: Mapped[Decimal | None] = mapped_column(Numeric)
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PlanMeal(Base):
    __tablename__ = "plan_meal"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_type: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    recipe_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.recipe.recipe_id"),
    )


class PlanDayProfile(Base):
    __tablename__ = "plan_day_profile"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.user_profile.profile_id"),
        primary_key=True,
    )
    kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric)
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric)
    whey_scoops: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=0)


class PlanDay(Base):
    __tablename__ = "plan_day"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    kcal: Mapped[Decimal | None] = mapped_column(Numeric)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric)
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric)


class PlanDayGroup(Base):
    __tablename__ = "plan_day_group"
    __table_args__ = {"schema": SCHEMA}

    plan_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.plan_run.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    food_group: Mapped[str] = mapped_column(Text, primary_key=True)
    daily_count: Mapped[int | None] = mapped_column(Integer)
    daily_portions: Mapped[Decimal | None] = mapped_column(Numeric)


class PipelineMetric(Base):
    __tablename__ = "pipeline_metric"
    __table_args__ = (
        UniqueConstraint(
            "metric_time", "metric_name", "correlation_id", name="uq_pipeline_metric_natural"
        ),
        {"schema": SCHEMA},
    )

    metric_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric)
    plan_run_id: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(Text)
