from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceSelectors(BaseModel):
    title: str
    ingredient_lines: str
    category: str
    rating: str
    servings: str
    difficulty: str
    nutrition: str = "div.nutrition.text"


class LocalHtmlSource(BaseModel):
    path: Path
    selectors: SourceSelectors


class SourcesSettings(BaseModel):
    local_html: LocalHtmlSource


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    batch_size: int = Field(default=5, ge=1, le=50)


class ParseSettings(BaseModel):
    fuzzy_min_score: float = Field(default=0.8, ge=0, le=1)
    llm_threshold: float = Field(default=0.7, ge=0, le=1)
    food_list_paths: list[Path] = Field(default_factory=list)
    synonyms_path: Path = Path("config/ingredient_synonyms.csv")
    unit_grams_path: Path = Path("config/unit_grams.csv")
    density_path: Path = Path("config/density_g_per_ml.csv")
    piece_grams_path: Path = Path("config/piece_grams.csv")
    default_portion_grams_path: Path = Path("config/default_portion_grams.csv")
    non_plant_terms_path: Path = Path("config/non_plant_terms.yaml")
    # Durable raw_text -> canonical/food_group overrides, seeded every parse run.
    overrides_path: Path = Path("config/ingredient_overrides.csv")


class NutritionSettings(BaseModel):
    cofid_path: Path | None = None
    # Manual per-100g nutrition seeded every enrich run; authoritative (e.g. a
    # product packet label), so it survives a cache wipe or full refresh.
    overrides_path: Path = Path("config/nutrition_overrides.csv")
    cofid_url: str = ""
    usda_api_key: str = ""
    coverage_min_ratio: float = Field(default=0.6, ge=0, le=1)
    open_food_facts_enabled: bool = True
    open_food_facts_user_agent: str = (
        "meal-planner - python - https://github.com/Matt1303/meal_planning"
    )
    open_food_facts_timeout: int = Field(default=10, gt=0)
    open_food_facts_countries: str = "en:united-kingdom"
    open_food_facts_lc: str = "en"
    cooking_oils: list[str] = Field(
        default_factory=lambda: [
            "olive oil",
            "extra virgin olive oil",
            "vegetable oil",
            "sunflower oil",
            "rapeseed oil",
            "canola oil",
            "sesame oil",
            "coconut oil",
            "groundnut oil",
            "peanut oil",
            "avocado oil",
            "ghee",
        ]
    )
    cooking_oil_absorption: float = Field(default=0.5, ge=0, le=1)
    llm_verify_enabled: bool = True
    llm_verify_score_threshold: float = Field(default=90.0, ge=0, le=120)
    llm_verify_batch_size: int = Field(default=20, ge=1, le=50)
    llm_macros_primary: bool = True
    llm_macros_batch_size: int = Field(default=30, ge=1, le=80)
    llm_macros_min_confidence: str = Field(default="medium", pattern="^(high|medium|low)$")


class PerPersonPortion(BaseModel):
    """A per-person serving-size override for a set of ingredient canonicals.

    grams maps profile name -> grams the person eats per serving. value_as names
    the canonical whose cached per-100g values to nutritionally value the portion
    with (e.g. "cooked rice" for cooked-weight rice defaults that resolve to dry
    rice canonicals). estimated_only restricts the override to no-quantity
    (default-portion) lines, leaving recipe-specified amounts untouched.
    """

    canonicals: list[str]
    grams: dict[str, float]
    value_as: str | None = None
    estimated_only: bool = True


class OptimizerSettings(BaseModel):
    min_rating: float = Field(default=3, ge=0, le=5)
    rating_weight: float = 1.0
    diversity_weight: float = 1.0
    recency_weight: float = 0.8
    slack_weight: float = 5.0
    spacing_weight: float = 2.0
    recency_half_life_days: int = Field(default=30, gt=0)
    calories_daily_min: int | None = 1800
    calories_daily_max: int | None = 2400
    fiber_daily_min: int | None = 30
    fiber_daily_max: int | None = None
    protein_daily_min: int | None = None
    protein_daily_max: int | None = None
    snack_optional: bool = False
    max_snacks_per_day: int = Field(default=1, ge=1, le=5)
    # category (case-insensitive substring of recipe categories) -> max snacks
    # of that category per day, e.g. {"smoothie": 1}.
    snack_category_limits: dict[str, int] = Field(default_factory=dict)
    max_recipe_repeats: int = Field(default=2, ge=1)
    solver_time_limit: int = Field(default=300, gt=0)
    solver_mip_gap: float = Field(default=0.05, ge=0, le=1)
    planning_horizon_days: int = Field(default=7, ge=1, le=30)
    calories_weekly_min: int | None = 12_600
    calories_weekly_max: int | None = 16_800
    fiber_weekly_min: int | None = 210
    protein_weekly_min: int | None = None
    weekly_group_portions_min: dict[str, float] = Field(default_factory=dict)
    include_non_plant: bool = False
    spacing_penalty_by_gap: dict[int, float] = Field(
        default_factory=lambda: {1: 1.0, 2: 0.3, 3: 0.1}
    )
    # Per-person serving sizes that override the shared per-serving amount for
    # specific ingredients in the solver and per-person macros (e.g. rice 400 g
    # Matt / 200 g Ellie). See PerPersonPortion.
    per_person_portions: list[PerPersonPortion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_ranges(self) -> OptimizerSettings:
        if (
            self.calories_daily_min is not None
            and self.calories_daily_max is not None
            and self.calories_daily_min > self.calories_daily_max
        ):
            raise ValueError("calories_daily_min must be <= calories_daily_max")
        if (
            self.calories_weekly_min is not None
            and self.calories_weekly_max is not None
            and self.calories_weekly_min > self.calories_weekly_max
        ):
            raise ValueError("calories_weekly_min must be <= calories_weekly_max")
        if (
            self.fiber_daily_min is not None
            and self.fiber_daily_max is not None
            and self.fiber_daily_min > self.fiber_daily_max
        ):
            raise ValueError("fiber_daily_min must be <= fiber_daily_max")
        if (
            self.protein_daily_min is not None
            and self.protein_daily_max is not None
            and self.protein_daily_min > self.protein_daily_max
        ):
            raise ValueError("protein_daily_min must be <= protein_daily_max")
        return self


class ProfileTargets(BaseModel):
    name: str
    display_name: str | None = None
    calories_daily_min: int | None = None
    calories_daily_max: int | None = None
    fiber_daily_min: int | None = None
    protein_daily_min: int | None = None
    protein_daily_max: int | None = None
    # meal_type -> recipe title to pin on that slot every day for this profile
    # (e.g. {"breakfast": "Matt Breakfast Smoothie"}).
    fixed_meals: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_ranges(self) -> ProfileTargets:
        if (
            self.calories_daily_min is not None
            and self.calories_daily_max is not None
            and self.calories_daily_min > self.calories_daily_max
        ):
            raise ValueError(
                f"profile '{self.name}': calories_daily_min must be <= calories_daily_max"
            )
        if (
            self.protein_daily_min is not None
            and self.protein_daily_max is not None
            and self.protein_daily_min > self.protein_daily_max
        ):
            raise ValueError(
                f"profile '{self.name}': protein_daily_min must be <= protein_daily_max"
            )
        return self


class HouseholdSettings(BaseModel):
    profiles: list[ProfileTargets] = Field(default_factory=list)
    shared_meal_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> HouseholdSettings:
        names = [p.name for p in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError(f"household profile names must be unique: {names}")
        return self


class TopUpFruit(BaseModel):
    """A fruit serving used to bridge a daily fruit/fibre shortfall."""

    name: str
    grams: float
    food_group: str
    kcal: float
    protein_g: float = 0.0
    fiber_g: float = 0.0
    fat_g: float = 0.0
    carbs_g: float = 0.0
    emoji: str = "🍎"


class TopUpSettings(BaseModel):
    """Post-solve gap-fillers: a whey shake for protein, fruit for the fruit
    Daily Dozen categories. Shown as guaranteed 'Top-up' snacks on a day that
    falls short, computed after the plan solves (not chosen by the optimiser)."""

    enabled: bool = True
    whey_label: str = "Whey protein shake (water)"
    whey_emoji: str = "🥤"
    whey_scoop_grams: float = 30.0
    whey_kcal: float = 114.0
    whey_protein_g: float = 22.0
    whey_fat_g: float = 1.8
    whey_carbs_g: float = 2.7
    whey_fiber_g: float = 0.0
    max_whey_scoops: int = 6
    fruits: list[TopUpFruit] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="MEAL_PLANNER_",
        case_sensitive=False,
        extra="ignore",
    )

    sources: SourcesSettings
    parse: ParseSettings = Field(default_factory=ParseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    nutrition: NutritionSettings = Field(default_factory=NutritionSettings)
    optimizer: OptimizerSettings = Field(default_factory=OptimizerSettings)
    household: HouseholdSettings = Field(default_factory=HouseholdSettings)
    topup: TopUpSettings = Field(default_factory=TopUpSettings)
    meal_types: list[str] = Field(default_factory=lambda: ["breakfast", "lunch", "dinner", "snack"])
    portion_sizes: dict[str, float] = Field(default_factory=dict)
    daily_dozen_targets: dict[str, int] = Field(default_factory=dict)

    @field_validator("portion_sizes")
    @classmethod
    def _portion_sizes_positive(cls, value: dict[str, float]) -> dict[str, float]:
        for group, size in value.items():
            if size <= 0:
                raise ValueError(f"portion size for {group} must be > 0")
        return value

    @model_validator(mode="after")
    def _portion_sizes_cover_targets(self) -> Settings:
        missing = sorted(set(self.daily_dozen_targets) - set(self.portion_sizes))
        if missing:
            raise ValueError(
                f"daily_dozen_targets has groups missing from portion_sizes: {missing}"
            )
        return self

    @model_validator(mode="after")
    def _household_shared_meals_subset(self) -> Settings:
        unknown = sorted(set(self.household.shared_meal_types) - set(self.meal_types))
        if unknown:
            raise ValueError(f"household.shared_meal_types contains unknown meal types: {unknown}")
        return self

    @classmethod
    def load(cls, path: Path | str | None = None) -> Settings:
        data = _load_yaml(path)
        return cls(**data)


def _load_yaml(path: Path | str | None) -> dict[str, Any]:
    cfg_path = Path(path) if path else Path(os.getenv("PIPELINE_CONFIG", "config/pipeline.yaml"))
    if not cfg_path.exists():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    with cfg_path.open() as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise TypeError(f"config root must be a mapping, got {type(loaded).__name__}")
    parse_block = loaded.setdefault("parse", {})
    if "food_list_paths" in loaded and "food_list_paths" not in parse_block:
        parse_block["food_list_paths"] = loaded["food_list_paths"]
    return loaded


def settings_to_redacted_dict(settings: Settings) -> dict[str, Any]:
    data = settings.model_dump(mode="json")
    if "llm" in data and isinstance(data["llm"], dict):
        if data["llm"].get("api_key"):
            data["llm"]["api_key"] = "***"
    if "nutrition" in data and isinstance(data["nutrition"], dict):
        if data["nutrition"].get("usda_api_key"):
            data["nutrition"]["usda_api_key"] = "***"
    return data


# helper used by tests / CLI
def info_for_validator(_: object, info: ValidationInfo) -> str:
    return info.field_name or ""
