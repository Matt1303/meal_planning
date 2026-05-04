# Plan to supercharge the meal planning pipeline

This plan focuses on: (1) reliable ingredient parsing and normalization, (2) nutrition and portion tracking, (3) multi-objective optimization for diversity, recency, ratings, and nutrition, and (4) faster, more maintainable workflows.

## 1) Target outcomes and constraints

### Outcomes
- High plant-based diversity across the week (unique ingredients + food groups).
- Avoid repetition by tracking last selected meals and recent ingredient usage.
- Meet nutrition targets (calories, fiber, and weekly food group portions).
- Respect meal ratings with a tunable weight and a strict minimum threshold.

### Key constraints to enforce
- Food group portions per week (e.g., 80g portion size, per category).
- Daily and weekly calorie range.
- Daily and weekly fiber minimum.
- Meal type coverage by day (breakfast, lunch, dinner, snacks).
  - Snacks can be simple fruit portions or a lightweight "snack recipe" category.
- Max repeats per recipe per week.
- Rating threshold (e.g., ratings < 3 excluded).

## 2) Data sources and ingestion strategy

### Inputs now
- Local HTML recipes in a directory ("180 recipes/Recipes").
- Food group list files (food_list.txt and food_list 2.txt).
- Existing Postgres schema and the current ingestion and planning scripts.

### Ingestion improvements
- Support multiple sources: local HTML folder, remote Git repo, or CSV.
- Store raw inputs for traceability (raw HTML + parsed fields).
- Use a stable parser and fall back to LLM only for ambiguous cases.

Example config for sources:
```yaml
sources:
  recipes:
    type: local_html
    path: "180 recipes/Recipes"
    selector:
      title: "h1"
      ingredient_lines: "div.ingredients.text p.line"
      category: "p.categories"
      rating: "p.rating"
      servings: "span[itemprop=recipeYield]"
```

## 3) Data model redesign (normalized and analyzable)

Introduce a structured schema to support nutrition, recency, and optimization at scale.

Database choice note
- Postgres is still a good default for this project: it supports concurrent reads/writes across services, scheduling, and long-term history.
- DuckDB is a strong alternative for local, single-user analytics and batch optimization, especially if most data lives in Parquet.
- Recommendation: keep Postgres as the system of record, optionally add a local DuckDB/Parquet cache for faster offline analysis.

Postgres approved.

### Core tables (new)
```sql
CREATE TABLE meal_planning.recipe (
  recipe_id SERIAL PRIMARY KEY,
  title TEXT NOT NULL UNIQUE,
  rating NUMERIC,
  servings TEXT,
  difficulty TEXT,
  source TEXT,
  last_modified TIMESTAMPTZ
);

CREATE TABLE meal_planning.recipe_ingredient (
  recipe_id INT NOT NULL,
  raw_text TEXT NOT NULL,
  ingredient_name TEXT,
  ingredient_canonical TEXT,
  quantity_value NUMERIC,
  quantity_unit TEXT,
  quantity_grams NUMERIC,
  per_serving_grams NUMERIC,
  food_group TEXT,
  portions NUMERIC,
  PRIMARY KEY (recipe_id, raw_text)
);

CREATE TABLE meal_planning.recipe_nutrition (
  recipe_id INT PRIMARY KEY,
  calories_kcal NUMERIC,
  fiber_g NUMERIC,
  protein_g NUMERIC,
  fat_g NUMERIC,
  carbs_g NUMERIC
);

CREATE TABLE meal_planning.meal_history (
  recipe_id INT NOT NULL,
  meal_type TEXT NOT NULL,
  planned_for DATE NOT NULL,
  PRIMARY KEY (recipe_id, meal_type, planned_for)
);

CREATE TABLE meal_planning.plan_config (
  config_id SERIAL PRIMARY KEY,
  name TEXT,
  min_rating NUMERIC,
  rating_weight NUMERIC,
  recency_half_life_days INT,
  calories_min INT,
  calories_max INT,
  fiber_min INT
);
```

## 4) Ingredient parsing and normalization

### Why change
LLM-only parsing is slow and inconsistent. The goal is to parse most ingredients deterministically, and only use LLM for edge cases.

### Proposed pipeline
1) Extract raw ingredient lines (as-is from HTML).
2) Parse quantity and unit with a deterministic parser.
3) Normalize ingredients to a canonical vocabulary.
4) Map to food groups using the list files plus synonym matching.

Example parsing logic:
```python
from quantulum3 import parser
from rapidfuzz import process

FOOD_GROUPS = {...}  # canonical items -> group

def parse_ingredient(line: str) -> dict:
    qty = parser.parse(line)
    quantity_value = qty[0].value if qty else None
    quantity_unit = qty[0].unit.name if qty else None

    # naive ingredient name fallback
    ingredient_name = line

    # fuzzy match to canonical list
    canonical, score, _ = process.extractOne(ingredient_name, FOOD_GROUPS.keys())
    if score < 80:
        canonical = None

    return {
        "ingredient_name": ingredient_name,
        "ingredient_canonical": canonical,
        "quantity_value": quantity_value,
        "quantity_unit": quantity_unit,
    }
```

## 5) Portion sizes and food group targets

Daily Dozen alignment
- Model Daily Dozen targets per day as configurable counts (not hard-coded). The standard targets for plant groups are typically:
  - Beans: 3
  - Berries: 1
  - Other Fruits: 3
  - Cruciferous Vegetables: 1
  - Greens: 2
  - Other Vegetables: 2
  - Flaxseeds: 1
  - Nuts and Seeds: 1
  - Herbs and Spices: 1
  - Whole Grains: 3
- Drinks and exercise are part of Daily Dozen but can be excluded unless you want to track them too. Note: exclude.

- Standardize portion size per food group (e.g., 80g for most groups).
- Compute portions per ingredient: $portions = grams / portion_size$.
- Daily Dozen count rule: one unique ingredient can only tick a group once per day (three apples still counts as one Other Fruit).
- Store portions per recipe and per serving for weekly constraints.

Example portion calculation:
```python
PORTION_SIZES = {
    "Beans": 80,
    "Berries": 80,
    "Other Fruits": 80,
    "Greens": 80,
    "Other Vegetables": 80,
    "Whole Grains": 80,
    "Nuts and Seeds": 30,
    "Herbs and Spices": 5,
}

DAILY_DOZEN_TARGETS = {
    "Beans": 3,
    "Berries": 1,
    "Other Fruits": 3,
    "Cruciferous Vegetables": 1,
    "Greens": 2,
    "Other Vegetables": 2,
    "Flaxseeds or Linseeds": 1,
    "Nuts and Seeds": 1,
    "Herbs and Spices": 1,
    "Whole Grains": 3,
  }

def compute_portions(grams: float, group: str) -> float:
    if grams is None or group not in PORTION_SIZES:
        return 0.0
    return grams / PORTION_SIZES[group]
```

Example Daily Dozen tick logic (unique ingredient per day):
```python
# portion_met[d,i] is 1 if ingredient i reaches portion size on day d
# Only count each ingredient once per day, even if it appears in multiple meals
daily_group_count[d,g] = sum(portion_met[d,i] for i in I if food_group[i] == g)
```

## 6) Nutrition enrichment

### Recommended strategy
- Prefer UK sources when possible (CoFID: Composition of Foods Integrated Dataset).
- Use USDA FoodData Central as a fallback when CoFID does not cover an ingredient.
- Compute nutrition per ingredient and aggregate to recipe-level totals.
- Cache results by canonical ingredient name.

### Example enrichment flow
```python
def recipe_nutrition(ingredients):
    totals = {"calories_kcal": 0, "fiber_g": 0}
    for ing in ingredients:
        nut = lookup_nutrition(ing["ingredient_canonical"])
        grams = ing["quantity_grams"] or 0
        totals["calories_kcal"] += nut["kcal_per_100g"] * grams / 100
        totals["fiber_g"] += nut["fiber_g_per_100g"] * grams / 100
    return totals
```

## 7) Optimization engine (diversity + nutrition + ratings + recency)

### Decision variables
- $x[r,d,m]$: select recipe $r$ on day $d$ for meal type $m$.
- Optional $s_k$: slack for soft constraints (penalized).

### Constraints
- One recipe per meal slot per day.
- Allow an optional snack slot if no suitable snack exists (penalize missing snack lightly instead of hard failure).
- Weekly food group portions within target ranges.
- Daily and weekly calories and fiber ranges.
- Maximum repeats per recipe per week.
- Ratings below threshold excluded.

### Objective (multi-term)
Maximize:
- Unique ingredients and food groups.
- Average rating (weighted).
- Recency penalty (prefer older recipes).
- Penalize constraint slacks.

Example objective structure (Pyomo style):
```python
obj = (
    w_diversity * sum(y[i] for i in I)
    + w_rating * sum(rating[r] * sum(x[r,d,m] for d in D for m in M) for r in R)
    - w_recency * sum(recency_penalty[r] * sum(x[r,d,m] for d in D for m in M) for r in R)
    - w_slack * sum(s[k] for k in SLACKS)
)
```

### Recency penalty
Compute a decay score based on last selected date:
```python
from math import exp

def recency_penalty(last_date, half_life_days=30, today=None):
    if last_date is None:
        return 0.0
    delta = (today - last_date).days
    # larger penalty for recent meals
    return exp(-delta / half_life_days)
```

## 8) Ratings and filtering

- Enforce a hard threshold for ratings (default min = 3).
- Add a weight slider for rating importance in objective.

Example filter:
```python
eligible = recipes[recipes.rating >= min_rating]
```

## 9) Orchestration and automation

### Recommended approach
- Use Prefect or Dagster to orchestrate ingestion -> parsing -> nutrition -> planning.
- Run weekly (cron or scheduler) and write a plan report to Postgres and a markdown summary.

### DAG outline
1) ingest_raw
2) parse_ingredients
3) normalize_food_groups
4) nutrition_enrichment
5) optimize_plan
6) write_plan

## 10) Observability and data quality

- Validate food group coverage (no missing category for canonical ingredients).
- Track percent of ingredients parsed deterministically vs LLM.
- LLM fallback should be provider-agnostic (OpenAI or Anthropic). If using Anthropic, default to Claude Sonnet 4.6.
- Log constraint satisfaction and slack usage.
- Maintain a plan report with diversity metrics.

Example quality checks:
```python
assert df["food_group"].isna().mean() < 0.05
assert df["quantity_grams"].notna().mean() > 0.7
```

## 11) Migration path

1) Create new tables alongside existing ones.
2) Build a one-time migration script to ingest local HTML and populate recipe + recipe_ingredient.
3) Add nutrition enrichment and cache table.
4) Implement the new optimizer and compare outputs with the current model.
5) Switch the pipeline to use new tables and deprecate old ones.

## 12) Immediate next steps (1-2 weeks)

- [x] Add ingestion of local HTML directory.
- [x] Implement deterministic ingredient parsing + fuzzy canonicalization.
- [x] Define portion sizes and compute portions per recipe.
- [x] Add nutrition enrichment (start with calories + fiber).
- [x] Implement recency tracking and rating filtering.
- [x] Replace the current objective with multi-term scoring.
- [x] Add configuration YAML with weights and targets.

## 13) Detailed TODO list

### Phase 0: Requirements and decisions (completed)
- [x] Confirm Daily Dozen targets to enforce (exclude drinks and exercise).
- [x] Confirm portion size rules per food group and any exceptions.
- [x] Define rating policy (minimum rating, weight slider range, and default values).
- [x] Define snack handling rule (fruit-only allowed vs snack recipes).
- [x] Choose primary nutrition data sources (CoFID first, USDA fallback).
- [x] Define the optimization horizon (weekly only vs rolling multi-week window).

### Phase 1: Data inventory and cleanup (completed)
- [x] Catalog all recipe sources (local HTML, remote repo, any CSVs).
- [x] Consolidate food list files into a single canonical list.
- [x] Define ingredient synonym list for common variants.
- [x] Create a mapping table for ambiguous ingredients and manual overrides.

### Phase 2: Schema and storage (completed)
- [x] Create new normalized tables alongside existing ones.
- [x] Add indexes for recipe_id, ingredient_canonical, and food_group.
- [x] Add a nutrition cache table keyed by canonical ingredient.
- [x] Add a plan run table to store metadata (config, solver status, timing).
- [x] Add a plan output table for per-day selections and metrics.

### Phase 3: Ingestion and parsing (completed)
- [x] Build a local HTML ingestion path with the same field mapping as current parser.
- [x] Store raw HTML (or raw ingredient lines) for traceability.
- [x] Implement deterministic quantity and unit parsing.
- [x] Normalize ingredient names with fuzzy matching and synonyms.
- [x] Add LLM fallback for low-confidence parses only.
- [x] Write parsed ingredient rows into recipe_ingredient.

### Phase 4: Portion and Daily Dozen logic (completed)
- [x] Implement grams normalization and unit conversion rules.
- [x] Compute per-serving grams and portion counts.
- [x] Implement Daily Dozen tick rule (unique ingredient per day).
- [x] Validate that portion sizes and ticks align with targets.

### Phase 5: Nutrition enrichment (completed)
- [x] Build CoFID lookup workflow and cache by canonical ingredient.
- [x] Implement USDA fallback when CoFID misses an ingredient.
- [x] Aggregate nutrition to recipe level (kcal, fiber; add more later).
- [x] Add data quality checks for missing nutrition coverage.

### Phase 6: Recency and ratings (completed)
- [x] Create meal_history entries for past plans (if any exist).
- [x] Implement recency scoring with half-life parameter.
- [x] Enforce rating threshold and add rating-weighted objective term.

### Phase 7: Optimization model redesign (completed)
- [x] Define objective weights and slack penalties in config.
- [x] Implement weekly nutrition constraints (kcal and fiber ranges).
- [x] Implement food group portion constraints (daily and weekly).
- [x] Add soft-constraint slacks where appropriate.
- [x] Validate feasibility and iterate on relaxation logic.

### Phase 8: Orchestration and automation (completed)
- [x] Choose orchestrator (Prefect or Dagster) and set up a simple flow.
- [x] Add scheduled weekly run with configurable inputs.
- [x] Persist plan outputs and metrics in Postgres.
- [x] Add a markdown report generator for human-readable output.

### Phase 9: Observability and QA (completed)
- [x] Add parse coverage metrics (deterministic vs LLM).
- [x] Add nutrition coverage metrics per recipe and per plan.
- [x] Add constraint satisfaction checks and slack summaries.
- [x] Track diversity metrics (unique ingredients, food groups).
- [x] Add regression tests for parser and optimizer constraints.

### Phase 10: Performance and cost controls (completed)
- [x] Add caching for ingredient parsing and nutrition lookups.
- [x] Batch LLM calls and apply strict fallback thresholds.
- [x] Add solver performance logging and timeouts.
- [x] Evaluate scaling approach if recipe count grows.

---

If you want, I can draft the exact schema migration, add the first version of the parser, and implement a new optimization module in code.