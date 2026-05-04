# Metrics

Metric names live in the MetricName StrEnum (src/meal_planner/metrics.py).
Every metric below is appended as a row in meal_planning.pipeline_metric
with metric_name, metric_value, metric_time, plan_run_id (if applicable),
and correlation_id (the run UUID).

| Name | Description |
|---|---|
| `ingest_recipes` | Number of recipe HTML files processed by the ingest step. |
| `ingest_ingredients` | Number of raw ingredient lines inserted during ingest. |
| `ingest_non_plant_filtered` | Recipes flagged as non-plant during ingest. |
| `ingest_selector_unmatched` | Selectors that returned zero matches in the first sampled file. |
| `parse_total` | Total ingredient rows considered by the parser this run. |
| `parse_cached` | Rows answered from the parse cache. |
| `parse_llm_used` | Rows that fell through to an LLM call. |
| `parse_llm_invalid_json` | LLM responses that failed JSON validation. |
| `parse_llm_retry_succeeded` | LLM retries that produced valid JSON on the second attempt. |
| `parse_quantulum_errors` | Exceptions raised by quantulum3 during parsing. |
| `nutrition_items_total` | Recipe ingredient rows considered for nutrition enrichment. |
| `nutrition_items_covered` | Rows for which a CoFID or USDA match was found. |
| `nutrition_coverage_ratio` | Ratio of covered nutrition items to total items. |
| `plan_unique_ingredients` | Unique canonical ingredients selected in the plan. |
| `plan_unique_food_groups` | Unique food groups represented in the plan. |
| `plan_daily_dozen_violations` | Sum of (target - count) across days where a Daily Dozen group fell short. |
| `portion_size_missing_groups` | Daily Dozen groups missing a portion size config entry. |
| `optimize_relaxation_level` | Relaxation tier reached by the optimiser (0=strict). |
| `solver_variable_count` | Total decision variables in the Pyomo model. |
| `solver_seconds` | Wall-clock seconds spent in solver.solve(). |
| `slack_total` | Sum of all slack variable values in the optimal solution. |
| `llm_cache_hit_tokens` | Tokens served from Anthropic prompt cache (per batch). |
