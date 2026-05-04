-- Create the postgres user if it does not exist
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'postgres') THEN
      CREATE ROLE postgres WITH LOGIN PASSWORD 'postgres';
   END IF;
END
$$;

-- Create the meal_planning database if it does not exist
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'meal_planning') THEN
      CREATE DATABASE meal_planning;
   END IF;
END
$$;


-- Connect to the meal_planning database
\c meal_planning;

-- Create the meal_planning schema if it does not exist
CREATE SCHEMA IF NOT EXISTS meal_planning;

-- Create the recipes table within the meal_planning schema if it does not exist
CREATE TABLE IF NOT EXISTS meal_planning.recipes (
    id serial PRIMARY KEY,
    title text NOT NULL UNIQUE,
    ingredients text,
    categories text,
    rating int,
    servings text,
    difficulty text,
    lastmodifieddate timestamp with time zone DEFAULT (now() AT TIME ZONE 'gmt')
);

-- Create the processed_recipes table if it does not exist
CREATE TABLE IF NOT EXISTS meal_planning.processed_recipes (
    title              text NOT NULL,
    ingredient         text NOT NULL,
    serving_quantity   text,
    category           text,
    breakfasts         integer NOT NULL DEFAULT 0,
    lunches            integer NOT NULL DEFAULT 0,
    dinner             integer NOT NULL DEFAULT 0,
    snacks             integer NOT NULL DEFAULT 0,
    lastmodifieddate   timestamp with time zone,
    UNIQUE (title, ingredient)
);

CREATE TABLE IF NOT EXISTS meal_planning.weekly_meal_plan (
    run_time           timestamp with time zone NOT NULL,
    week_number        integer            NOT NULL,
    day                integer           NOT NULL,
    breakfast          text,
    lunch              text,
    dinner             text,
    snack              text,
    beans               integer,
    berries             integer,
    other_fruits        integer,
    cruciferous_vegetables integer,
    greens              integer,
    other_vegetables    integer,
    flaxseeds           integer,
    nuts_and_seeds      integer,
    herbs_and_spices    integer,
    whole_grains        integer
);

CREATE TABLE IF NOT EXISTS meal_planning.recipe (
   recipe_id       serial PRIMARY KEY,
   title           text NOT NULL UNIQUE,
   rating          numeric,
   servings        text,
   servings_count  numeric,
   difficulty      text,
   categories      text,
   source          text,
   last_modified   timestamp with time zone
);

CREATE TABLE IF NOT EXISTS meal_planning.recipe_source (
   recipe_id   integer REFERENCES meal_planning.recipe(recipe_id) ON DELETE CASCADE,
   source_path text,
   raw_html    text,
   ingested_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meal_planning.recipe_meal_type (
   recipe_id   integer REFERENCES meal_planning.recipe(recipe_id) ON DELETE CASCADE,
   meal_type   text NOT NULL,
   PRIMARY KEY (recipe_id, meal_type)
);

CREATE TABLE IF NOT EXISTS meal_planning.recipe_ingredient (
   recipe_id           integer REFERENCES meal_planning.recipe(recipe_id) ON DELETE CASCADE,
   raw_text            text NOT NULL,
   ingredient_name     text,
   ingredient_canonical text,
   quantity_value      numeric,
   quantity_unit       text,
   quantity_grams      numeric,
   per_serving_grams   numeric,
   food_group          text,
   portions            numeric,
   portion_met         boolean,
   PRIMARY KEY (recipe_id, raw_text)
);

CREATE TABLE IF NOT EXISTS meal_planning.ingredient_nutrition_cache (
   ingredient_canonical text PRIMARY KEY,
   kcal_per_100g        numeric,
   fiber_g_per_100g     numeric,
   source               text,
   updated_at           timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meal_planning.ingredient_parse_cache (
   raw_text             text PRIMARY KEY,
   ingredient_name      text,
   ingredient_canonical text,
   quantity_value       numeric,
   quantity_unit        text,
   quantity_grams       numeric,
   food_group           text,
   updated_at           timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meal_planning.ingredient_override (
   raw_text             text PRIMARY KEY,
   ingredient_canonical text,
   food_group           text,
   updated_at           timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meal_planning.recipe_nutrition (
   recipe_id           integer PRIMARY KEY REFERENCES meal_planning.recipe(recipe_id) ON DELETE CASCADE,
   calories_kcal       numeric,
   fiber_g             numeric,
   per_serving_kcal    numeric,
   per_serving_fiber_g numeric
);

CREATE TABLE IF NOT EXISTS meal_planning.meal_history (
   recipe_id   integer REFERENCES meal_planning.recipe(recipe_id) ON DELETE CASCADE,
   meal_type   text NOT NULL,
   planned_for date NOT NULL,
   PRIMARY KEY (recipe_id, meal_type, planned_for)
);

CREATE TABLE IF NOT EXISTS meal_planning.plan_config (
   config_id             serial PRIMARY KEY,
   name                  text,
   min_rating            numeric,
   rating_weight         numeric,
   recency_half_life_days integer,
   calories_min          integer,
   calories_max          integer,
   fiber_min             integer,
   snack_optional        boolean DEFAULT false
);

CREATE TABLE IF NOT EXISTS meal_planning.plan_run (
   plan_run_id   serial PRIMARY KEY,
   run_time      timestamp with time zone NOT NULL,
   config_id     integer REFERENCES meal_planning.plan_config(config_id),
   status        text,
   solver_status text,
   total_kcal    numeric,
   total_fiber   numeric,
   solver_seconds numeric,
   slack_total   numeric
);

CREATE TABLE IF NOT EXISTS meal_planning.plan_meal (
   plan_run_id integer REFERENCES meal_planning.plan_run(plan_run_id) ON DELETE CASCADE,
   day         integer NOT NULL,
   meal_type   text NOT NULL,
   recipe_id   integer REFERENCES meal_planning.recipe(recipe_id),
   PRIMARY KEY (plan_run_id, day, meal_type)
);

CREATE TABLE IF NOT EXISTS meal_planning.plan_day (
   plan_run_id integer REFERENCES meal_planning.plan_run(plan_run_id) ON DELETE CASCADE,
   day         integer NOT NULL,
   kcal        numeric,
   fiber_g     numeric,
   PRIMARY KEY (plan_run_id, day)
);

CREATE TABLE IF NOT EXISTS meal_planning.plan_day_group (
   plan_run_id   integer REFERENCES meal_planning.plan_run(plan_run_id) ON DELETE CASCADE,
   day           integer NOT NULL,
   food_group    text NOT NULL,
   daily_count   integer,
   daily_portions numeric,
   PRIMARY KEY (plan_run_id, day, food_group)
);

CREATE TABLE IF NOT EXISTS meal_planning.pipeline_metric (
   metric_time  timestamp with time zone DEFAULT now(),
   metric_name  text NOT NULL,
   metric_value numeric
);

CREATE INDEX IF NOT EXISTS idx_recipe_ingredient_recipe_id ON meal_planning.recipe_ingredient (recipe_id);
CREATE INDEX IF NOT EXISTS idx_recipe_ingredient_canonical ON meal_planning.recipe_ingredient (ingredient_canonical);
CREATE INDEX IF NOT EXISTS idx_recipe_ingredient_food_group ON meal_planning.recipe_ingredient (food_group);
CREATE INDEX IF NOT EXISTS idx_recipe_meal_type_meal ON meal_planning.recipe_meal_type (meal_type);
CREATE INDEX IF NOT EXISTS idx_meal_history_planned_for ON meal_planning.meal_history (planned_for);
