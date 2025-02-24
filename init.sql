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

CREATE TABLE IF NOT EXISTS meal_planning.ingredients (
    id serial PRIMARY KEY,
    title text NOT NULL UNIQUE,
    ingredients text,
    categories text,
    rating int,
    servings text,
    difficulty text,
    lastmodifieddate timestamp with time zone DEFAULT (now() AT TIME ZONE 'gmt')
);
