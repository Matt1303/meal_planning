# meal_planning research report v2 (2026-05-02)

A deep walkthrough of the repository as it stands today, with emphasis on the
new modular pipeline that has largely replaced (but not yet retired) the
original three-service Docker stack documented in [research.md](research.md).

---

## 1. High-level shape of the project

This repo solves a single end-to-end problem: given a folder of recipe HTML
files, produce a weekly meal plan that respects nutrition, ingredient
diversity, recipe ratings, recency, and Daily Dozen-style food-group targets,
with everything persisted in Postgres.

There are **two parallel implementations** of the same idea living side by
side:

1. **Legacy stack** — three Docker services orchestrated through
   [docker-compose.yaml](docker-compose.yaml):
   - [ingest_data.py](ingest_data.py) clones a Git repo of HTML recipes into
     `meal_planning.recipes`.
   - [data_processor.py](data_processor.py) calls OpenAI GPT-4o to extract
     normalized ingredients into `meal_planning.processed_recipes`.
   - [meal_planner.py](meal_planner.py) runs a Pyomo/GLPK MILP and writes
     `meal_planning.weekly_meal_plan`.
   - Shared utilities in [db_manager.py](db_manager.py) and [prompts.py](prompts.py).

2. **New modular pipeline** — a Python package [pipeline/](pipeline/) plus a
   Prefect flow in [flows/](flows/), driven by [config/pipeline.yaml](config/pipeline.yaml).
   It targets a richer normalized schema (the `recipe`, `recipe_ingredient`,
   `recipe_nutrition`, `plan_run`, `plan_meal`, `plan_day`, `plan_day_group`,
   `meal_history`, `pipeline_metric`, etc. tables added to [init.sql](init.sql)).

The new pipeline is what [plan.md](plan.md) tracks (every TODO item is checked
off) and what [research.md](research.md) anticipates. The legacy stack is still
on disk and still referenced by `docker-compose.yaml`, but isn't used by the
new flow. Both write into the same Postgres database, just into different
tables.

---

## 2. Repository layout

```
meal_planning/
├── 180 recipes/Recipes/        # 182 HTML recipe files (the canonical input)
├── config/
│   ├── pipeline.yaml           # All knobs for the new pipeline
│   ├── food_list_canonical.txt # Cleaned-up Daily Dozen vocabulary
│   └── ingredient_synonyms.csv # raw → canonical lowercase mapping
├── data/
│   └── source_inventory.json   # written by source_inventory step
├── flows/
│   ├── weekly_plan_flow.py     # Prefect flow wrapping pipeline.run_pipeline
│   └── deploy_weekly.py        # Prefect deployment, cron Mon 06:00 UTC
├── pipeline/                   # New modular pipeline (Phases 0–10 in plan.md)
│   ├── config.py               # YAML loader
│   ├── db.py                   # SQLAlchemy engine, wait_for_db, helpers
│   ├── source_inventory.py     # Snapshots configured sources to JSON
│   ├── ingest_html.py          # BS4 ingestion of local HTML
│   ├── backfill_history.py     # Imports legacy weekly_meal_plan → meal_history
│   ├── parse_ingredients.py    # quantulum3 + regex + fuzzy + LLM fallback
│   ├── food_list.py            # Loads food groups + synonyms
│   ├── nutrition.py            # CoFID + USDA enrichment, cached
│   ├── optimizer.py            # Pyomo MILP with multi-term objective
│   ├── report.py               # Markdown plan report
│   └── run_pipeline.py         # Imperative orchestration (no Prefect)
├── tests/
│   ├── test_parse_helpers.py   # _strip_quantity, _unit_to_grams
│   └── test_recency.py         # _recency_penalty
├── tools/
│   └── generate_docx_report.py # DOCX + matplotlib/seaborn charts
├── reports/                    # Generated PNGs (kcal/fiber, daily dozen heatmap, top ingredients, repeats)
├── init.sql                    # Postgres schema for both old and new tables
├── docker-compose.yaml         # Legacy stack (postgres + pgadmin + 3 services)
├── ingest_data.py              # Legacy ingestion service
├── data_processor.py           # Legacy LLM categorisation service
├── meal_planner.py             # Legacy Pyomo planner
├── db_manager.py               # Legacy DB helper
├── prompts.py                  # Legacy LLM prompts
├── food_list.txt / food_list 2.txt  # Legacy duplicate lists (still referenced)
├── plan.md, research.md        # Earlier design docs (ground truth for v2)
├── plan_report.md              # Latest markdown plan output (Day 1–7, kcal/fiber zeroes — see §11)
├── report.docx                 # Latest DOCX report
└── requirements_*.txt          # Pinned dependencies, split per service
```

---

## 3. Configuration model

Everything the new pipeline needs lives in [config/pipeline.yaml](config/pipeline.yaml)
and is loaded via [pipeline/config.py](pipeline/config.py:5) (`PIPELINE_CONFIG`
env override is supported). Highlights:

- **`sources.local_html.path`** = `"180 recipes/Recipes"` — local directory of
  HTML files, no Git clone.
- **`sources.local_html.selectors`** — CSS selectors for `title`,
  `ingredient_lines`, `category`, `rating`, `servings`, `difficulty`. The
  expected DOM is e.g. [Moroccan Lentil Soup.html:122-160](Moroccan Lentil Soup.html#L122-L160):
  `<h1 itemprop="name" class="name">…`, `<p class="line" itemprop="recipeIngredient"><strong>1.2</strong> litres …`,
  etc.
- **`food_list_paths`** — three files merged, last write wins: the canonical
  one at [config/food_list_canonical.txt](config/food_list_canonical.txt) plus
  the legacy [food_list.txt](food_list.txt) and `food_list 2.txt` (these two
  are byte-identical 2,991-byte copies).
- **`portion_sizes`** — grams per "1 portion" per food group: 80g for most
  groups, 30g for Nuts and Seeds, 10g for Flaxseeds, 5g for Herbs and Spices.
- **`daily_dozen_targets`** — ticks per day per group, e.g. Beans: 3, Berries:
  1, Other Fruits: 3, Whole Grains: 3.
- **`nutrition.cofid_path`** = `data/cofid.xlsx` (download URL is empty by
  default), `usda_api_key` falls back to `USDA_API_KEY` env var.
- **`llm`** — `provider: anthropic`, `model: claude-sonnet-4-6`, batch_size 5,
  min_confidence 0.8 (used for both LLM gating and fuzzy match cutoff).
- **`optimizer`** — `min_rating: 3`, `rating_weight: 1.0`,
  `diversity_weight: 1.0`, `recency_weight: 0.8`, `slack_weight: 5.0`,
  `recency_half_life_days: 30`, kcal range 1800–2400/day, fiber 30g/day,
  weekly mins kcal 12,600–16,800 / fiber 210g, `max_recipe_repeats: 2`,
  `solver_time_limit: 60s`, `planning_horizon_days: 7`,
  `snack_optional: false`, `weekly_group_portions_min: {}` (empty means use
  daily target × horizon as default).
- **Meal types**: `breakfast, lunch, dinner, snack` (lower-case singulars in
  the new schema; legacy used the plural form `breakfasts/lunches/snacks`).

---

## 4. Database schema (init.sql)

Two generations of tables coexist in `meal_planning` schema.

**Legacy tables** (still created, written to by the Docker services, also
backfilled into `meal_history` by the new pipeline):

- `recipes(id, title UNIQUE, ingredients TEXT, categories TEXT, rating INT,
  servings TEXT, difficulty TEXT, lastmodifieddate)`
- `processed_recipes(title, ingredient, serving_quantity, category,
  breakfasts, lunches, dinner, snacks, lastmodifieddate, UNIQUE(title,ingredient))`
- `weekly_meal_plan(run_time, week_number, day, breakfast, lunch, dinner,
  snack, beans, berries, other_fruits, …, whole_grains)` — this is the table
  legacy `meal_planner.py` writes a 7-row weekly summary into. Note: the SQL
  uses singular column names (`beans`, `berries`, …) with no `_count` suffix,
  while [meal_planner.py:138](meal_planner.py#L138) writes columns named
  `beans_count`, `berries_count`, …; [research.md](research.md) calls this
  out as a known mismatch.

**New normalized tables**:

- `recipe(recipe_id PK, title UNIQUE, rating, servings, servings_count, difficulty, categories, source, last_modified)`
- `recipe_source(recipe_id FK, source_path, raw_html, ingested_at)` — keeps the raw HTML for traceability.
- `recipe_meal_type(recipe_id, meal_type, PK(recipe_id, meal_type))` — derived from `categories`.
- `recipe_ingredient(recipe_id, raw_text, ingredient_name, ingredient_canonical, quantity_value, quantity_unit, quantity_grams, per_serving_grams, food_group, portions, portion_met, PK(recipe_id, raw_text))`.
- `ingredient_parse_cache(raw_text PK, …)` and `ingredient_nutrition_cache(ingredient_canonical PK, kcal_per_100g, fiber_g_per_100g, source, updated_at)` — hot caches keyed by raw line and canonical name respectively.
- `ingredient_override(raw_text PK, ingredient_canonical, food_group)` — manual mapping table consulted before fuzzy matching.
- `recipe_nutrition(recipe_id PK, calories_kcal, fiber_g, per_serving_kcal, per_serving_fiber_g)`.
- `meal_history(recipe_id, meal_type, planned_for, PK(recipe_id, meal_type, planned_for))` — recency input.
- `plan_config(config_id PK, name, min_rating, rating_weight, recency_half_life_days, calories_min, calories_max, fiber_min, snack_optional)` — exists but isn't currently written (config lives in YAML).
- `plan_run(plan_run_id PK, run_time, config_id, status, solver_status, total_kcal, total_fiber, solver_seconds, slack_total)` — one row per optimisation.
- `plan_meal(plan_run_id, day, meal_type, recipe_id, PK(plan_run_id, day, meal_type))` — selected recipe per slot.
- `plan_day(plan_run_id, day, kcal, fiber_g)` — daily totals derived from per-serving nutrition.
- `plan_day_group(plan_run_id, day, food_group, daily_count, daily_portions)` — Daily Dozen evidence per day.
- `pipeline_metric(metric_time, metric_name, metric_value)` — append-only metrics log used everywhere (ingest counts, parse cache hit/LLM-used, nutrition coverage, plan diversity, daily dozen violations, etc.).

**Indexes**: `recipe_ingredient(recipe_id|ingredient_canonical|food_group)`,
`recipe_meal_type(meal_type)`, `meal_history(planned_for)`.

---

## 5. New pipeline — step by step

[pipeline/run_pipeline.py:11](pipeline/run_pipeline.py#L11) wires the steps in
imperative order; [flows/weekly_plan_flow.py](flows/weekly_plan_flow.py) wraps
each in a Prefect `@task` and exposes a single `weekly_plan_flow`.

### 5.1 Source inventory
[pipeline/source_inventory.py](pipeline/source_inventory.py) writes the
`sources` block of the YAML to `data/source_inventory.json`. Cheap traceability
artefact, no DB writes.

### 5.2 Local HTML ingestion
[pipeline/ingest_html.py:47](pipeline/ingest_html.py#L47)

- Reads every `*.html` in `180 recipes/Recipes/` (sorted, alphabetical).
- Uses BS4 `select_one` + the configured selectors to pull `title`,
  `category`, `rating[value]`, `servings`, `difficulty`.
- `_parse_servings_count` ([ingest_html.py:9](pipeline/ingest_html.py#L9))
  scans for digit/period runs and returns: single number → that number;
  range like `"4-6"` → mean of the first two; falls back to None.
- `last_modified` uses **filesystem mtime** (`os.path.getmtime`), unlike the
  legacy `ingest_data.py` which used Git's `--format=%cd`.
- Upserts the recipe (`ON CONFLICT (title)`), inserts `recipe_source` (raw
  HTML body — *appended on every run*, the table has no UNIQUE constraint, so
  re-runs grow it monotonically), then for `recipe_meal_type`:
  - Wipes existing rows for that `recipe_id`.
  - Splits `categories` on `,`, lower-cases, and **trims a single trailing
    `s`** ([ingest_html.py:130](pipeline/ingest_html.py#L130)).
  - **Bug to be aware of**: `"Lunches".lower().rstrip-of-final-s` → `"lunche"`,
    not `"lunch"`. So lunch-tagged recipes get stored as meal type `lunche`
    and never match the optimizer's allowed-meal set (which expects `"lunch"`
    from `meal_types` in YAML). Same logic correctly handles `Breakfasts→breakfast`
    and `Snacks→snack`; `Dinner` already singular.
- Each `<p class="line">` becomes a `recipe_ingredient` row with only `raw_text`
  populated (parsing happens later).
- Emits `pipeline_metric` rows `ingest_recipes` and `ingest_ingredients`.

### 5.3 History backfill
[pipeline/backfill_history.py](pipeline/backfill_history.py) walks the legacy
`weekly_meal_plan` table, locates whichever of `breakfast/lunch/dinner/snack`
(plural or singular) columns exist, looks up `recipe_id` by title, and
inserts `meal_history(recipe_id, meal_type, run_time::date)` rows. Idempotent
via `ON CONFLICT DO NOTHING`. This means a fresh DB with no legacy
`weekly_meal_plan` rows produces no history (recency penalty all zero).

### 5.4 Ingredient parsing
[pipeline/parse_ingredients.py:162](pipeline/parse_ingredients.py#L162) is
the most complex module. Per row in `recipe_ingredient` where any of the
parse fields is NULL:

1. **Cache lookup** — `ingredient_parse_cache.raw_text` keyed by the raw line.
   Treated as authoritative if `quantity_grams IS NOT NULL`.
2. **Quantity parse** — first `quantulum3.parser.parse(raw_text)`; if it
   throws, the module disables itself for the rest of the run via the
   `quantulum_ok` guard. Falls back to `_regex_parse_quantity`
   ([parse_ingredients.py:91](pipeline/parse_ingredients.py#L91)) which
   handles ranges (`200-300g`, mid-pointed), multipliers (`2 x 400g`),
   fractions (`1 1/2 cups`), and a fixed unit list.
3. **Grams conversion** — `_unit_to_grams`
   ([parse_ingredients.py:35](pipeline/parse_ingredients.py#L35)) treats
   volume units as if 1ml=1g and uses fixed conversions: tsp=5g, tbsp=15g,
   cup=240g, oz=28.35g, lb=453.6g.
4. **Ingredient name** — the raw line with all numbers and units stripped
   (`_strip_quantity`).
5. **Canonicalisation** — three layers in order of precedence:
   a. `ingredient_override(raw_text)` — DB-managed manual mapping.
   b. `ingredient_synonyms.csv` — exact-match lookup by lowercased raw text
      (loaded once into a dict by `load_synonyms`).
   c. `rapidfuzz.process.extractOne` against the keys of the merged food
      group dict, accepted if `score/100 ≥ min_confidence` (0.8).
6. **Food group** — lookup `food_groups[canonical]` (built from the three
   food-list paths via [pipeline/food_list.py:22](pipeline/food_list.py#L22)).
7. **Per-serving + portion** — `per_serving_grams = quantity_grams /
   servings_count`; `portions = per_serving / portion_size[group]`;
   `portion_met = per_serving ≥ portion_size`.
8. **LLM fallback** — only if `canonical is None` *and* the cache row was
   missing, batched at `llm_batch_size` (default 5). Provider switch supports
   both Anthropic (`claude-sonnet-4-6`) and OpenAI (`gpt-4o`); prompt
   constrains `food_group` to the known list and asks for JSON. Strips
   markdown fences before `json.loads`.
9. Updates `recipe_ingredient` and upserts into `ingredient_parse_cache`.
10. Emits metrics `parse_total`, `parse_cached`, `parse_llm_used`.

The food-list parser ([food_list.py:5](pipeline/food_list.py#L5)) has a
peculiar rule: a non-blank line is treated as a category header *only if it
is sandwiched between blank lines on both sides*. That requires the section
header to start a new block and have an empty separator line beneath it.
Both [food_list.txt](food_list.txt) and
[config/food_list_canonical.txt](config/food_list_canonical.txt) follow that
convention; `current` is reset to `None` whenever a blank line is seen, which
also means the *first* item of the next section can be misclassified as a
header if formatted unusually.

### 5.5 Nutrition enrichment
[pipeline/nutrition.py:108](pipeline/nutrition.py#L108)

- Loads CoFID workbook from `data/cofid.xlsx` if present (or downloads from
  `cofid_url`). The repo currently has no CoFID file shipped — `data/`
  contains only `source_inventory.json`, so on a stock checkout the nutrition
  step has only USDA available.
- `_guess_columns` heuristically picks the food-name, kcal, and fiber columns
  by substring matching on lowered column names.
- Per ingredient: cache hit on `ingredient_nutrition_cache` returns
  immediately; otherwise fuzzy CoFID lookup (≥80 score), else USDA `/foods/search`
  with `pageSize=1`. Result is cached.
- Aggregates *per serving* across `recipe_ingredient` rows
  (`kcal += kcal_per_100g * per_serving_grams / 100`), then computes recipe
  totals as `per_serving × servings_count` and upserts `recipe_nutrition`.
- Emits `nutrition_items_total`, `nutrition_items_covered`,
  `nutrition_coverage_ratio`.
- Subtle: the function passes the active SQLAlchemy `Connection` into
  `lookup_nutrition` as the parameter named `engine`. It works because
  `Connection.execute(text(...))` is valid — but the naming is misleading.

### 5.6 Optimisation
[pipeline/optimizer.py:51](pipeline/optimizer.py#L51) — Pyomo MILP, GLPK
solver. The modelling is a faithful upgrade of the legacy planner:

**Sets**: D = `{1..horizon_days}` (default 7), M = `meal_types`,
R = recipes filtered to `rating ≥ min_rating`, I = unique
`ingredient_canonical` values that have `portion_met = TRUE` somewhere,
G = food groups present in `daily_dozen_targets`.

**Decision variables**:
- `x[r,d,m] ∈ {0,1}` — recipe r picked for slot (d,m).
- `z[d,i] ∈ {0,1}` — ingredient i present (with met portion) on day d.
- `y[i] ∈ {0,1}` — ingredient i used somewhere in the week.
- Slacks (continuous, ≥0): `slack_group[d,g]`, `slack_weekly_group[g]`,
  optional `slack_cal_min[d]`, `slack_cal_max[d]`, `slack_fiber[d]`,
  `slack_weekly_cal_min`, `slack_weekly_cal_max`, `slack_weekly_fiber`,
  `slack_snack[d]` (only if `snack_optional`).

**Constraints**:
- `meal_slot`: exactly one recipe per (d,m) slot, except snack slots may be
  filled by slack when `snack_optional`.
- `allowed`: `x[r,d,m] ≤ allowed_meal[r,m]` where `allowed_meal` comes from
  `recipe_meal_type` (defaults to all-allowed if the table is empty).
- `repeat_limit`: each recipe appears ≤ `max_recipe_repeats` times across
  the week.
- `ingredient_use`: `z[d,i]` only if some selected recipe contributes a met
  portion of i on day d.
- `ingredient_global_lower/upper`: link `y[i]` to `Σz[d,i]`.
- `group_min`: `Σ_{i∈g} z[d,i] + slack ≥ targets[g]` per day per group
  (Daily Dozen, unique-ingredient counting).
- `weekly_group_min`: `Σ portions[r,g] · x[r,d,m] + slack ≥ target`. Default
  target is `daily_target[g] × horizon_days`; overridable per-group via
  `weekly_group_portions_min`.
- Calorie/fiber daily and weekly bounds — each soft via its own slack.

**Objective** (`maximize`):
```
diversity_weight · Σ y[i]
+ rating_weight · Σ rating[r] · Σ x[r,d,m]
- recency_weight · Σ recency[r] · Σ x[r,d,m]
- slack_weight · Σ all_slacks
```
`recency[r] = exp(-Δdays / half_life_days)` from the most recent
`meal_history.planned_for` (0 if never planned). Recent picks therefore carry
a *larger* penalty, pushing the solver toward older recipes.

**Solver**: `glpk` via `SolverFactory`; `tmlim` set from `solver_time_limit`.
Non-optimal termination raises (no auto-relaxation, unlike the legacy
planner's `MAX_RELAXATIONS=15` decrement loop).

### 5.7 Plan persistence
[pipeline/optimizer.py:306](pipeline/optimizer.py#L306) `write_plan(...)`:

- Inserts `plan_run` and gets back `plan_run_id`.
- For each (day, meal_type) inserts `plan_meal`.
- For each day: sums `per_serving_kcal`/`per_serving_fiber_g` across selected
  recipes, writes `plan_day`. Then groups portion-met ingredients by
  `food_group` and writes `plan_day_group(daily_count, daily_portions)` per
  target group; tracks `daily_violations` for groups under target.
- Inserts `meal_history` for each (recipe, meal_type, today) so the *next*
  run sees these as recent.
- Updates the parent `plan_run` row with `total_kcal`/`total_fiber`.
- Emits `plan_unique_ingredients`, `plan_unique_food_groups`,
  `plan_daily_dozen_violations`.

### 5.8 Reports

- [pipeline/report.py](pipeline/report.py) writes a markdown digest to
  `plan_report.md`: per-day meals, kcal+fiber, Daily Dozen counts and
  portions.
- [tools/generate_docx_report.py](tools/generate_docx_report.py) is invoked
  separately. It pulls the latest `plan_run`, builds four PNGs into
  `reports/` (`kcal_fiber` line chart, `daily_dozen` heatmap,
  `top_ingredients` barh, `repeats` scatter timeline), and stitches them into
  `report.docx` with `python-docx`. The current `reports/plan_4_*.png`
  artefacts are from `plan_run_id=4`.

### 5.9 Orchestration
- [flows/weekly_plan_flow.py](flows/weekly_plan_flow.py) wraps each pipeline
  step as a Prefect 2.x `@task` and runs them sequentially in a `@flow`.
- [flows/deploy_weekly.py](flows/deploy_weekly.py) creates a Deployment with
  `CronSchedule("0 6 * * 1")` (Mondays 06:00 UTC). Note this uses the
  **Prefect 2.x** `Deployment.build_from_flow` API (matching `prefect==2.16.5`
  in [requirements_common.txt](requirements_common.txt)); not the newer
  `flow.deploy()` API.

---

## 6. Legacy stack — what's still on disk

The legacy implementation is fully self-contained and still buildable, but
not invoked by the new flow.

- [ingest_data.py](ingest_data.py): clones `REPO_URL` (default
  `https://github.com/Matt1303/recipe_html_pages`) into `CLONE_DIR`, parses
  every HTML, and joins ingredients with `; ` into a single string in
  `meal_planning.recipes`. Uses Git log for `lastmodifieddate`.
- [data_processor.py](data_processor.py): reads `meal_planning.recipes`,
  builds an in-prompt `category_dict` from `food_list.txt`, asks GPT-4o to
  return JSON of `{ingredient, serving_quantity, category}` items, flattens
  with `json_normalize`, attaches one-hot meal-type flags, upserts into
  `meal_planning.processed_recipes`. Filters by
  `LAST_MODIFIED_DATE` cutoff (defaults to *today midnight*, which means
  nothing is processed unless overridden — [research.md](research.md)
  flagged this).
- [meal_planner.py](meal_planner.py): the original Pyomo model. Hardcoded
  CATEGORY_REQUIREMENTS — note these differ from the new YAML: legacy uses
  `Other Fruits: 2` and `Whole Grains: 2` (vs 3/3 in YAML), excludes
  `Flaxseeds or Linseeds`, and uses `MAX_RELAXATIONS = 15` decrement on
  infeasibility. Writes one row per day to `weekly_meal_plan` with
  `_count`-suffixed group columns. Comment in [research.md:138-141](research.md#L138-L141)
  flags the `_count` vs no-suffix mismatch and the `breakfast/breakfasts`
  pluralisation mismatch.
- [db_manager.py](db_manager.py): SQLAlchemy + psycopg2 helpers, parameterised
  upsert via `execute_values`. The most recent commits (`4df1d91`,
  `48661ee`) explicitly fixed SQL injection in this file —
  `remove_deleted_recipes` now uses `WHERE title = ANY(:titles)` instead of
  string-formatting titles into the query.
- [prompts.py](prompts.py): instructs GPT-4o to keep only the ten plant
  groups, *exclude oils, milks, pastes, dried herbs/spices and anything
  "ground"*, and compute per-serving quantities. The narrowing matters: it's
  why the legacy `processed_recipes` only contains plant items, while the new
  pipeline ingests everything.

---

## 7. Recipe HTML format

The 182 files in [180 recipes/Recipes/](180 recipes/Recipes/) plus the
loose [Moroccan Lentil Soup.html](Moroccan Lentil Soup.html) follow a
consistent structure:

```html
<h1 itemprop="name" class="name">Moroccan Lentil Soup</h1>
<p itemprop="aggregateRating" class="rating" value="0"></p>
<p itemprop="recipeCategory" class="categories">How Not To Diet</p>
<span itemprop="difficulty">Easy</span>
<span itemprop="recipeYield">4</span>
<div class="ingredients text">
  <p class="line" itemprop="recipeIngredient"><strong>1.2</strong> litres Vegetable Broth …</p>
  <p class="line" itemprop="recipeIngredient"><strong>200</strong>g dried black or red lentils</p>
  …
</div>
```

Implications:
- The strong tags around the quantity are stripped by BS4's `get_text(...)`,
  but quantulum3 still sees the bare number followed by the unit string.
- `categories` is a comma-separated list of *meal types and program tags*
  (e.g. `"Lunches, Dinner, How Not To Diet"`). The pipeline depends on this
  to populate `recipe_meal_type`, with the lunches→lunche pluralisation bug
  noted in §5.2.
- Some recipes are non-plant (e.g. "Simply Perfect Beef Spag Bol", "Lemon
  Chicken Orzo", "Chicken And Mushroom Risotto" — all of which appear in
  [plan_report.md](plan_report.md)). The new pipeline does not exclude these
  the way `prompts.py` does.

---

## 8. Tests

Tiny but pinpointed at fragile bits:

- [tests/test_parse_helpers.py](tests/test_parse_helpers.py): asserts
  `_strip_quantity("2 cups chopped kale") == "chopped kale"` and a couple of
  `_unit_to_grams` cases.
- [tests/test_recency.py](tests/test_recency.py): `_recency_penalty` returns
  >0 for a date one day in the past and 0 for `None`.
- [pytest.ini](pytest.ini) sets `pythonpath = .` so the `pipeline.*` imports
  resolve.

Coverage is light — there are no integration tests against a real Postgres,
no end-to-end optimizer assertions, no parser tests for the regex
range/multiplier/fraction code paths.

---

## 9. Dependencies and runtime layout

[requirements_common.txt](requirements_common.txt) (used by the base Docker
image) pins Python 3.12.4 and includes everything the new pipeline needs:
`pandas 2.2.2`, `SQLAlchemy 2.0.34`, `psycopg2-binary 2.9.10`, `GitPython
3.1.43`, `beautifulsoup4 4.12.3`, `quantulum3[classifier] 0.9.0`,
`rapidfuzz 3.10.1`, `pyyaml 6.0.2`, `requests 2.32.3`, `openpyxl 3.1.5`,
`prefect 2.16.5`, `anthropic 0.34.2`, `setuptools 75.1.0`.

Service-specific tops-ups: `openai 1.65.3` for the legacy data_processor;
`pyomo 6.8.2` (+ apt `glpk-utils`) for both meal_planner stacks.
[requirements_dev.txt](requirements_dev.txt) adds `mypy 1.11.1`, `pytest
8.3.2`, `python-docx 1.1.2`, `matplotlib 3.9.2`, `seaborn 0.13.2` (the last
three are only consumed by `tools/generate_docx_report.py`).

---

## 10. Configuration / environment knobs

[.env.example](.env.example) drives both stacks:

- `DB_USER/PASSWORD/HOST/PORT/NAME` — Postgres connection
  ([pipeline/db.py:7](pipeline/db.py#L7) reads the same vars with sensible
  defaults).
- `REPO_URL`, `CLONE_DIR`, `TABLE_NAME`, `SCHEMA_NAME` — legacy ingest only.
- `LAST_MODIFIED_DATE`, `NUM_RECIPES`, `FORCE_UPDATE_PROCESSED` — legacy
  data_processor only.
- `OPENAI_API_KEY`, `LLM_API_KEY`, `USDA_API_KEY` — LLM and nutrition
  fallbacks. The new parser reads `LLM_API_KEY` if `llm.api_key` in YAML is
  empty; legacy reads `OPENAI_API_KEY`.
- `FOOD_LIST_PATH` — legacy data_processor (the new pipeline uses
  `food_list_paths` from YAML).
- `PIPELINE_CONFIG` (not in `.env.example`) — overrides the YAML path read
  by `load_config`.

The `.env` file is git-ignored; `.env.example` is the documented template
and was last updated to mention Anthropic Console alongside OpenAI.

---

## 11. Specificities, gotchas and observable issues

These are worth flagging because they're not obvious from a casual read:

1. **Two parallel implementations writing to the same DB.** The new pipeline
   doesn't drop or migrate the legacy tables; it adds new ones beside them
   and only *reads* from `weekly_meal_plan` to seed `meal_history`.
   `init.sql` creates both sets unconditionally on first boot.

2. **Meal-type pluralisation bug.** [pipeline/ingest_html.py:130](pipeline/ingest_html.py#L130)
   strips a single trailing `s` from each lowercased category token, which
   maps `Breakfasts→breakfast`, `Snacks→snack`, but `Lunches→lunche`
   (correct singular is `lunch`). Result: lunch slots will only be filled by
   recipes whose category is exactly `Lunch` (no `s`), which is rarely how
   the source HTML labels them.

3. **`config/food_list_canonical.txt` last-write wins over `food_list.txt`.**
   `load_food_groups` walks the list of paths in order and writes to a dict
   keyed by lowercased item, so the canonical file is intended to override
   the legacy one. In practice the canonical file fixes things like
   `YamsYucca` (still merged in `food_list.txt:201`), and uses singular forms
   for many produce items.

4. **`recipe_source` grows on every run.** That table has no PK or unique
   constraint, so re-running `ingest_local_html` multiplies the rows. If
   that's not desired, an upsert keyed on `(recipe_id, source_path)` would
   be the natural fix.

5. **`plan_report.md` shows zeros everywhere for kcal/fiber and Daily Dozen
   counts.** That output is from `plan_run_id=4`. It points at one of two
   real failure modes:
   - **No CoFID file shipped.** `data/cofid.xlsx` doesn't exist and
     `cofid_url` is empty. Without a USDA API key, `recipe_nutrition` stays
     empty, so daily kcal/fiber sums to 0 and the optimiser's calorie/fiber
     constraints rely entirely on slack to satisfy.
   - **`portion_met` rarely true.** Daily Dozen counts depend on
     `portion_met = TRUE` for an ingredient on a day. That requires both
     `quantity_grams` (parsed unit→g) *and* `servings_count`, *and*
     `per_serving_grams ≥ portion_size[group]`. Many recipe ingredients
     (e.g. spices in tsp) parse to small per-serving grams below the 5g
     threshold, and many lines (e.g. "1 red pepper, chopped") have no unit
     so `quantity_grams` is `None`. The optimiser then satisfies daily group
     minimums via `slack_group[d,g]` rather than real ingredients.

6. **Non-plant recipes aren't filtered.** `plan_report.md` includes "Simply
   Perfect Beef Spag Bol", "Lemon Chicken Orzo", "Chicken And Mushroom
   Risotto" — the new pipeline never had a plant-only filter analogous to
   the GPT-4o prompt in `prompts.py`.

7. **`servings_count` parsing is forgiving but lossy.** `"4-6"` becomes 5.0,
   `"makes 12"` → 12.0, `"3.5"` → 3.5. Anything without digits returns
   `None`, and downstream `per_serving_grams` and `portions` go `None` too.

8. **Quantulum3 self-disables on first failure.** The `quantulum_ok` flag in
   `parse_ingredients.py` is global to a run; one exception silently turns
   off LLM-quality unit parsing for every subsequent line and falls through
   to the regex parser only.

9. **Fuzzy match cutoff and LLM trigger share a knob.** Both gates use the
   same `llm.min_confidence` (0.8): items below it skip canonicalisation and
   become LLM candidates. Tuning this affects two unrelated things at once.

10. **Optimizer hard-fails on infeasibility.** Unlike legacy `meal_planner.py`,
    the new optimizer raises immediately if GLPK returns anything other than
    `optimal`. Because every "hard" thing is wrapped in slack, infeasibility
    in practice means the model itself is broken (e.g. `R` empty, `meal_slot`
    cardinality unsatisfiable) rather than nutrition targets unreachable.

11. **`plan_config` table is unused.** All optimiser knobs come from YAML;
    nothing inserts a row into `plan_config`, and `plan_run.config_id` is
    always NULL.

12. **Recency penalty bootstraps from the legacy table.** If
    `weekly_meal_plan` is empty (fresh DB), `meal_history` is empty after
    backfill, and every `recency[r]` is 0 — the recency term contributes
    nothing on the first run. After the first plan, `write_plan` inserts
    today's selections into `meal_history`, so subsequent runs penalise them.

13. **`solver_time_limit: 60` is short.** With 182 candidate recipes, 28
    slots, ~hundreds of canonical ingredients, and ten food groups, the
    model can be sizeable; the time limit doubles as a way of forcing GLPK
    to return whatever it has — which then trips the "non-optimal" raise.

14. **Anthropic provider already plumbed.** `parse_ingredients._llm_parse_batch`
    supports both `openai` and `anthropic` providers; the YAML default is
    `claude-sonnet-4-6`. Note the model id is the SDK-style id; `anthropic`
    package version pinned at `0.34.2` (older than the current SDK) is
    consistent with that id format. Switching to a newer model will require
    bumping the SDK and confirming the API surface.

15. **Recent security fixes in legacy code.** `git log` shows
    `4df1d91 Security: Fix SQL injection vulnerabilities in db_manager.py`
    and `48661ee Fix security and data integrity issues`. Worth keeping in
    mind if you're extending the legacy stack — not all string-format SQL
    has necessarily been audited (the new `pipeline/*` modules are
    consistently parameterised via SQLAlchemy `text(:bind)`).

---

## 12. End-to-end summary

Running `python -m pipeline.run_pipeline` (or the Prefect flow) does the
following from a populated Postgres + recipe folder:

1. Snapshot YAML sources to `data/source_inventory.json`.
2. Read every HTML in `180 recipes/Recipes/`, upsert into `recipe`,
   `recipe_source`, `recipe_meal_type`, and seed `recipe_ingredient` with raw
   lines.
3. Backfill `meal_history` from any legacy `weekly_meal_plan` rows.
4. Parse ingredients deterministically (quantulum3 + regex), canonicalise
   via override → synonyms → fuzzy match, fall back to an LLM batch call only
   for unmatched lines, and persist parse cache + recipe_ingredient updates.
5. Enrich `recipe_nutrition` from CoFID (if present) and USDA (if API key);
   cache by canonical ingredient.
6. Solve the weekly MILP (Pyomo+GLPK), maximising diversity + ratings minus
   recency minus slack, subject to daily/weekly nutrition and Daily Dozen
   constraints.
7. Persist `plan_run`, `plan_meal`, `plan_day`, `plan_day_group`,
   `meal_history` for today, and a `plan_report.md`.
8. Optionally run [tools/generate_docx_report.py](tools/generate_docx_report.py)
   to produce charts in `reports/` and `report.docx` covering the latest
   plan run plus the previous three.

`pipeline_metric` is appended to at every stage, giving a poor-man's
observability log: counts of ingested recipes/ingredients, parser
cache-hit/LLM-used, nutrition coverage ratio, plan diversity, and Daily
Dozen violations.
