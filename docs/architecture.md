# Architecture

## Stage flow

```
+----------------+    +----------------+    +-----------------+    +--------------+    +-------------+    +----------+
| source         |    | ingest         |    | parse           |    | nutrition    |    | optimise    |    | report   |
| inventory      |--> | (BS4 + plant   |--> | (quantulum +    |--> | (CoFID +     |--> | (Pyomo +    |--> | (md /    |
| (yaml->json)   |    |  classifier)   |    |  regex + LLM)   |    |  USDA)       |    |  GLPK)      |    |  docx /  |
+----------------+    +----------------+    +-----------------+    +--------------+    +-------------+    |  html)   |
                                                                                                          +----------+
```

Every stage:
- accepts a typed `Settings` object (pydantic-settings; see [docs/configuration.md](configuration.md))
- writes to Postgres (`meal_planning` schema) via repository functions in `src/meal_planner/db/`
- emits metrics via `record_metric(...)` keyed on `correlation_id` so a single run is queryable

## Database schema

Defined by `Base.metadata` in `src/meal_planner/db/models.py`, applied by Alembic.

Core entities:
- `recipe` (with `is_plant_based` flag set at ingest)
- `recipe_source` (raw HTML, idempotent on `recipe_id, source_path`)
- `recipe_meal_type`
- `recipe_ingredient` (with parsed canonical, grams, food group, portion_met flag)
- `recipe_nutrition` (kcal, fiber, plus protein/fat/carbs)

Caches:
- `ingredient_parse_cache` keyed on raw text
- `ingredient_nutrition_cache` keyed on canonical name (with match_score, source)
- `ingredient_override` for manual fixes

Plan output:
- `plan_config` (YAML snapshot per run)
- `plan_run` (one row per optimisation, with relaxation_level and correlation_id)
- `plan_meal`, `plan_day`, `plan_day_group`
- `meal_history` (drives recency penalty in subsequent runs)

Observability:
- `pipeline_metric` (append-only, indexed by correlation_id)
- views: `pipeline_run` (rollup) and `latest_plan_summary`

## LLM port

`src/meal_planner/llm/` exposes a `LLMClient` protocol with one method:

```python
class LLMClient(Protocol):
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse: ...
```

Three adapters: `AnthropicLLM`, `OpenAILLM`, `NullLLM`. Selected by
`get_llm_client(settings.llm)`. The Anthropic adapter sends the food-group
list as a `cache_control: ephemeral` system block, so subsequent batches in a
run reuse the cache.

## Optimisation model

Pyomo MILP solved by GLPK. Variables:

- `x[r,d,m] ∈ {0,1}` — recipe r picked for day d, meal m
- `z[d,i] ∈ {0,1}` — ingredient i contributed a met portion on day d
- `y[i] ∈ {0,1}` — ingredient i appeared somewhere in the week
- continuous `slack_*` variables for soft constraints

Objective:

```
maximize  diversity_weight · Σ y[i]
        + rating_weight · Σ rating[r] · Σ x[r,d,m]
        - recency_weight · Σ recency[r] · Σ x[r,d,m]
        - slack_weight · Σ slacks
```

If GLPK reports anything other than `optimal` at the strictest level, the
optimiser walks down a `RelaxationLevel` ladder
(`STRICT → DROP_DAILY_NUTRITION → DROP_WEEKLY_GROUPS → DROP_GROUP_TARGETS`)
until it finds a feasible solution. The level reached is recorded in
`plan_run.relaxation_level` and surfaced in the report.

## Orchestration

Two interchangeable runners:

- **Prefect 2.16** — `flows/weekly_plan_flow.py` + `prefect.yaml` deployment
  on cron `0 6 * * 1`. Each step is a task; nutrition has retries=2,
  ingest+parse have retries=1.
- **cron + CLI** — `ops/cron/weekly_plan.sh` is a self-contained script that
  sources `.env`, activates the venv, and runs `meal-planner run`.

Both bind a fresh UUID4 `correlation_id` at flow start, propagated through
structlog and into every `pipeline_metric` row.

## Run lifecycle

1. New `correlation_id` minted.
2. `source_inventory` snapshots the YAML to `data/source_inventory.json`.
3. `ingest_local_html` reads HTML, classifies plant/non-plant, upserts
   `recipe`, `recipe_source` (idempotent), `recipe_meal_type` (normalised
   meal types, no more `lunche` bug), and seeds `recipe_ingredient` rows
   with raw text.
4. `parse_ingredients` resolves canonical names via override → synonym →
   fuzzy match → LLM fallback (only if a real LLM is configured), persists
   parse cache.
5. `enrich_nutrition` looks up CoFID then USDA, fails the run if coverage
   < `nutrition.coverage_min_ratio`.
6. `optimize_plan` filters `is_plant_based = TRUE` (unless overridden),
   builds the MILP, solves with GLPK.
7. `write_plan` snapshots config to `plan_config`, persists results, writes
   `meal_history` so the next run sees today's selections as recent.
8. `generate_report` writes md (always), html (optional), docx (optional)
   into `reports/plan_<id>.*`.
