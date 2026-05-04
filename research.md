# meal_planning research report (2026-04-30)

## Overview
This repository implements a containerized pipeline that ingests recipe HTML files from a Git repository, extracts structured recipe data into Postgres, uses an LLM to normalize and categorize plant-based ingredients, and then runs a weekly optimization model to assemble a meal plan that satisfies daily category requirements. The system is split into three main services plus a shared database layer.

Primary flow (high level):
1) meal_ingest clones a recipe HTML repo and upserts raw recipe fields into Postgres.
2) data_processor reads recipes, uses an LLM to extract normalized ingredients with categories, and upserts a processed table.
3) meal_planner runs an optimization model (Pyomo + GLPK) to produce a weekly plan and stores a summary table.

## Core components and behavior

### Ingestion service
- Implementation: [ingest_data.py](ingest_data.py)
- Purpose: clone recipe HTML pages and parse them into a structured recipes table.
- Source: a Git repository defined by REPO_URL (example in [.env.example](.env.example)).
- Parsing details:
  - HTML structure assumptions:
    - Title in the first h1.
    - Ingredients inside div.ingredients.text, with each ingredient in a p.line tag.
    - Category in p.categories.
    - Rating in p.rating (value attribute).
    - Servings in span[itemprop=recipeYield].
    - Difficulty in span[itemprop=difficulty].
  - Ingredients are joined into a single string with "; " separators, then normalized to single spaces.
  - Last modified date is derived from Git history (git log -1) for each HTML file, not file mtime.
- Database write:
  - Uses SQLAlchemy engine and a manual upsert statement with ON CONFLICT (title).
  - Target schema and table are controlled by TABLE_NAME and DB env vars.

### Processing service
- Implementation: [data_processor.py](data_processor.py)
- Purpose: convert raw ingredient strings into structured ingredient records with categories and per-serving quantities.
- Inputs:
  - Recipes table from Postgres (meal_planning.recipes).
  - A food list file mapped into the container (FOOD_LIST_PATH).
  - Prompt templates in [prompts.py](prompts.py).
- Food list parsing:
  - The parser expects the food list format to use blank lines to separate category headers from items.
  - It derives a category dictionary by forward-filling the most recent category header.
- LLM usage:
  - Uses OpenAI SDK with model gpt-4o.
  - Prompt includes a dict of recipe fields plus an item-to-category dict built from food_list.
  - Output must be JSON with keys: title and ingredients (list of ingredient, serving_quantity, category).
- Processing controls:
  - LAST_MODIFIED_DATE defaults to current UTC date (midnight) if not set.
  - NUM_RECIPES limits processing to the first N rows after sorting by lastmodifieddate.
  - FORCE_UPDATE_PROCESSED controls whether already-processed titles are skipped.
  - Only recipes with lastmodifieddate > cutoff are processed.
- Output:
  - Results are flattened into a row-per-ingredient table using json_normalize.
  - One-hot meal type flags are derived from the original recipes categories and merged into the processed data.
  - Upsert into processed_recipes uses a unique constraint on (title, ingredient).
  - A cleanup step removes processed titles that no longer exist in the source recipes table.

### Planning service
- Implementation: [meal_planner.py](meal_planner.py)
- Purpose: build a weekly meal plan that maximizes unique ingredients while meeting daily category requirements.
- Data source: meal_planning.processed_recipes table in Postgres.
- Model structure (Pyomo):
  - Decision variables:
    - x[r,d,m] = 1 if recipe r is selected for day d and meal type m.
    - z[d,i] = 1 if ingredient i appears on day d.
    - y[i] = 1 if ingredient i appears at least once in the week.
  - Constraints:
    - Exactly one recipe per day and meal type.
    - Recipe max occurrences (default 5 per week).
    - Ingredients implied by selected recipes.
    - Daily minimum counts per category, using CATEGORY_REQUIREMENTS.
  - Objective: maximize the number of unique ingredients used in the week (sum of y[i]).
- Feasibility strategy:
  - If the solver is infeasible, requirements are relaxed one at a time (decrementing the first non-zero requirement) up to MAX_RELAXATIONS.
- Output:
  - Constructs a weekly summary (7 rows, one per day) and inserts into weekly_meal_plan.
  - Week number is computed as max(week_number) + 1 in the target table.

### Database utilities
- Implementation: [db_manager.py](db_manager.py)
- Provides:
  - Connection management to Postgres using env vars.
  - A wait_for_db retry loop.
  - Table existence checks and generalized upsert via psycopg2 execute_values.
  - Helper to delete processed recipes that no longer exist in source table.

## Data model and schema
Defined in [init.sql](init.sql).

- meal_planning.recipes
  - Raw recipe data from HTML: title, ingredients, categories, rating, servings, difficulty, lastmodifieddate.
  - title is unique.

- meal_planning.processed_recipes
  - One row per (title, ingredient).
  - Includes serving_quantity, category, and meal-type flags (breakfasts, lunches, dinner, snacks).
  - Unique constraint on (title, ingredient).

- meal_planning.weekly_meal_plan
  - One row per day of the week and per plan run.
  - Fields for run_time, week_number, day, plus meal selections and category counts.

## Configuration and runtime
- Environment variables are documented in [.env.example](.env.example).
- Core variables:
  - DB_* for database access.
  - REPO_URL and CLONE_DIR for ingestion.
  - TABLE_NAME and SCHEMA_NAME for target tables.
  - LAST_MODIFIED_DATE, NUM_RECIPES, FORCE_UPDATE_PROCESSED for LLM processing.
  - OPENAI_API_KEY and FOOD_LIST_PATH for LLM and food list input.

## Docker and service layout
Defined in [docker-compose.yaml](docker-compose.yaml).

- base
  - Built from [Dockerfile.base](Dockerfile.base) with requirements_common.txt.
- postgres
  - Standard Postgres container with init.sql mounted for schema setup.
- pgadmin
  - Optional UI for database inspection.
- meal_ingest
  - Built from [Dockerfile_meal_ingest](Dockerfile_meal_ingest), runs ingest_data.py.
- data_processor
  - Built from [Dockerfile_data_processor](Dockerfile_data_processor), runs data_processor.py and installs OpenAI SDK.
- meal_planner
  - Built from [Dockerfile_meal_planner](Dockerfile_meal_planner), installs pyomo and GLPK utilities and runs meal_planner.py.

## Data assets
- [food_list.txt](food_list.txt) provides the category vocabulary used to map ingredients to 10 plant food categories.
- [README.md](README.md) is currently a long-form list of plant foods (and includes a Fermented Foods section that is not used in processing).
- [data_processor.ipynb](data_processor.ipynb) contains exploratory notebook code for DB access and food list parsing; it is not used by the services.

## Specificities, assumptions, and notable quirks
- The LLM prompt explicitly excludes oils, milks, pastes, and ground spices. It expects fresh herbs/spices only. This narrows the ingredient set and may drop items that appear in recipes.
- The ingestion parser is tightly coupled to the HTML structure (specific tags and class names). If the HTML layout changes, fields may be empty.
- LAST_MODIFIED_DATE defaults to the current UTC date at midnight. With no override, only recipes modified after today will be processed.
- data_processor sorts recipes by lastmodifieddate and then applies head(N) when NUM_RECIPES is set, so the earliest N rows are considered before the cutoff filter.
- Category matching for meal types is case-sensitive to title case in source categories (e.g., "Breakfasts"), while stored flags are lowercase (breakfasts, lunches, dinner, snacks).
- The food list parser relies on blank lines to detect category headers. Any formatting changes in food_list.txt could break category detection.
- Column name mismatches to verify:
  - weekly_meal_plan in init.sql uses breakfast, lunch, dinner, snack, while meal_planner writes breakfasts, lunches, dinner, snacks.
  - weekly_meal_plan in init.sql uses category columns without a _count suffix, while meal_planner writes beans_count, berries_count, etc.
  These mismatches could cause insert errors or missing columns.
- food_list.txt contains a merged token "YamsYucca" in Other Vegetables, which could reduce matching quality for those items.

## Dependency summary
- Common: pandas, SQLAlchemy, psycopg2-binary, GitPython, beautifulsoup4.
- data_processor: OpenAI SDK.
- meal_planner: pyomo and system package glpk-utils.

## How the pieces fit together (condensed)
- meal_ingest populates meal_planning.recipes from HTML.
- data_processor reads recipes, calls the LLM, writes meal_planning.processed_recipes.
- meal_planner reads processed_recipes, optimizes weekly choices, writes meal_planning.weekly_meal_plan.
