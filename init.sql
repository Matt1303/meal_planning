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
