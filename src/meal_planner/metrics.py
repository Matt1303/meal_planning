from __future__ import annotations

from enum import StrEnum


class MetricName(StrEnum):
    INGEST_RECIPES = "ingest_recipes"
    INGEST_INGREDIENTS = "ingest_ingredients"
    INGEST_NON_PLANT_FILTERED = "ingest_non_plant_filtered"
    INGEST_SELECTOR_UNMATCHED = "ingest_selector_unmatched"

    PARSE_TOTAL = "parse_total"
    PARSE_CACHED = "parse_cached"
    PARSE_LLM_USED = "parse_llm_used"
    PARSE_LLM_INVALID_JSON = "parse_llm_invalid_json"
    PARSE_LLM_RETRY_SUCCEEDED = "parse_llm_retry_succeeded"
    PARSE_QUANTULUM_ERRORS = "parse_quantulum_errors"

    NUTRITION_ITEMS_TOTAL = "nutrition_items_total"
    NUTRITION_ITEMS_COVERED = "nutrition_items_covered"
    NUTRITION_COVERAGE_RATIO = "nutrition_coverage_ratio"

    PLAN_UNIQUE_INGREDIENTS = "plan_unique_ingredients"
    PLAN_UNIQUE_FOOD_GROUPS = "plan_unique_food_groups"
    PLAN_DAILY_DOZEN_VIOLATIONS = "plan_daily_dozen_violations"

    PORTION_SIZE_MISSING_GROUPS = "portion_size_missing_groups"
    OPTIMIZE_RELAXATION_LEVEL = "optimize_relaxation_level"
    SOLVER_VARIABLE_COUNT = "solver_variable_count"
    SOLVER_SECONDS = "solver_seconds"
    SLACK_TOTAL = "slack_total"

    LLM_CACHE_HIT_TOKENS = "llm_cache_hit_tokens"


METRIC_DESCRIPTIONS: dict[MetricName, str] = {
    MetricName.INGEST_RECIPES: "Number of recipe HTML files processed by the ingest step.",
    MetricName.INGEST_INGREDIENTS: "Number of raw ingredient lines inserted during ingest.",
    MetricName.INGEST_NON_PLANT_FILTERED: "Recipes flagged as non-plant during ingest.",
    MetricName.INGEST_SELECTOR_UNMATCHED: "Selectors that returned zero matches in the first sampled file.",
    MetricName.PARSE_TOTAL: "Total ingredient rows considered by the parser this run.",
    MetricName.PARSE_CACHED: "Rows answered from the parse cache.",
    MetricName.PARSE_LLM_USED: "Rows that fell through to an LLM call.",
    MetricName.PARSE_LLM_INVALID_JSON: "LLM responses that failed JSON validation.",
    MetricName.PARSE_LLM_RETRY_SUCCEEDED: "LLM retries that produced valid JSON on the second attempt.",
    MetricName.PARSE_QUANTULUM_ERRORS: "Exceptions raised by quantulum3 during parsing.",
    MetricName.NUTRITION_ITEMS_TOTAL: "Recipe ingredient rows considered for nutrition enrichment.",
    MetricName.NUTRITION_ITEMS_COVERED: "Rows for which a CoFID or USDA match was found.",
    MetricName.NUTRITION_COVERAGE_RATIO: "Ratio of covered nutrition items to total items.",
    MetricName.PLAN_UNIQUE_INGREDIENTS: "Unique canonical ingredients selected in the plan.",
    MetricName.PLAN_UNIQUE_FOOD_GROUPS: "Unique food groups represented in the plan.",
    MetricName.PLAN_DAILY_DOZEN_VIOLATIONS: "Sum of (target - count) across days where a Daily Dozen group fell short.",
    MetricName.PORTION_SIZE_MISSING_GROUPS: "Daily Dozen groups missing a portion size config entry.",
    MetricName.OPTIMIZE_RELAXATION_LEVEL: "Relaxation tier reached by the optimiser (0=strict).",
    MetricName.SOLVER_VARIABLE_COUNT: "Total decision variables in the Pyomo model.",
    MetricName.SOLVER_SECONDS: "Wall-clock seconds spent in solver.solve().",
    MetricName.SLACK_TOTAL: "Sum of all slack variable values in the optimal solution.",
    MetricName.LLM_CACHE_HIT_TOKENS: "Tokens served from Anthropic prompt cache (per batch).",
}
