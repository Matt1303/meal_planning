import os
import time
import pandas as pd
from pandas import json_normalize
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from openai import OpenAI
import json
from datetime import datetime, timezone, timedelta
import psycopg2
from psycopg2.extras import execute_values
import logging
from prompts import INSTRUCTIONS, OUTPUT_FORMAT

# Initialize OpenAI client using API key from environment variables.
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configure logging.
logging.basicConfig(
    level=logging.INFO,  # Use DEBUG for more detailed output.
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Environment Variables with Defaults ---
default_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
LAST_MODIFIED_DATE = os.getenv("LAST_MODIFIED_DATE", default_date)

if LAST_MODIFIED_DATE == "":
    LAST_MODIFIED_DATE = default_date

logger.info("LAST_MODIFIED_DATE: %s", LAST_MODIFIED_DATE)

NUM_RECIPES = os.getenv("NUM_RECIPES")
if NUM_RECIPES is not None and NUM_RECIPES != "":
    try:
        NUM_RECIPES = int(NUM_RECIPES)
    except ValueError:
        logger.info("If defining, NUM_RECIPES environment variable must be an integer.")
        NUM_RECIPES = None
else:
    NUM_RECIPES = None

if NUM_RECIPES is None:
    logger.info("NUM_RECIPES: %s - all valid recipes will be processed", NUM_RECIPES)
else:
    logger.info("NUM_RECIPES: %s - only the first %d valid recipes will be processed", NUM_RECIPES, NUM_RECIPES)

# Flag to indicate whether to force re‑processing of recipes even if already processed.
FORCE_UPDATE_PROCESSED = os.getenv("FORCE_UPDATE_PROCESSED", "True").lower() in ["true", "1", "yes"]
if FORCE_UPDATE_PROCESSED:
    logger.info("FORCE_UPDATE_PROCESSED: %s - any recipes updated on or after LAST_MODIFIED_DATE will be updated in processed_recipes postgres table", FORCE_UPDATE_PROCESSED)
else:
    logger.info("FORCE_UPDATE_PROCESSED: %s - only recipes not already processed will be added to processed_recipes postgres table", FORCE_UPDATE_PROCESSED)

def get_food_list():
    """
    Reads the food list file from the path specified in the environment variable
    and returns a DataFrame with the processed data.
    """
    file_path = os.getenv("FOOD_LIST_PATH")
    if not file_path:
        raise ValueError("FOOD_LIST_PATH environment variable is not set!")
    
    with open(file_path, 'r') as file:
        lines = file.readlines()

    data = []
    current_category = None
    for i, line in enumerate(lines):
        line = line.strip()
        if line:  # Non-empty line
            # Simple logic: if previous and next lines are blank, treat as category.
            if current_category is None and (i == 0 or not lines[i-1].strip()) and (i + 1 < len(lines) and not lines[i+1].strip()):
                current_category = line
                data.append({'Category': line, 'Item': None})
            else:
                data.append({'Category': None, 'Item': line})
        else:
            current_category = None
    df = pd.DataFrame(data)
    df['Category'] = df['Category'].ffill()
    food_list_df = df.dropna(subset=['Item'])
    return food_list_df

class RecipeProcessor:
    def __init__(self, recipes_df, category_dict, instructions, output_format):
        self.recipes_df = recipes_df.sort_values(by='lastmodifieddate', ascending=True)
        self.category_dict = category_dict
        self.instructions = instructions
        self.output_format = output_format
    
    def get_response(self, prompt, temperature=0.0):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Error getting response from OpenAI: %s", e)
            return None    

    def process_recipe(self, recipe):
        dictionaries = f"ingredients_dict = {recipe}\ncategory_dict = {self.category_dict}"
        prompt = self.instructions + self.output_format + f"\n\nHere are the input dictionaries:\n```{dictionaries}```"
        
        response = self.get_response(prompt)
        if response is None:
            raise ValueError("No response received from OpenAI.")

        if response.startswith('```json'):
            response = response[7:-3].strip()

        try:
            json_object = json.loads(response)
        except json.JSONDecodeError as e:
            logger.error("Error parsing JSON from OpenAI response: %s", e)
            logger.error("Response was: %s", response)
            json_object = None

        return json_object
    
    def process_all_recipes(self, last_modified_date, num_recipes=None, existing_titles=None):
        results = []
        
        # Convert last_modified_date to datetime if needed.
        if isinstance(last_modified_date, str):
            last_modified_date = datetime.strptime(last_modified_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        
        logger.info("Using last modified date cutoff: %s", last_modified_date)
        
        if num_recipes is None:
            num_recipes = len(self.recipes_df)
        
        for i in range(num_recipes):
            recipe = self.recipes_df.iloc[i].to_dict()
            recipe_last_modified_date = recipe['lastmodifieddate']
            if isinstance(recipe_last_modified_date, pd.Timestamp):
                recipe_last_modified_date = recipe_last_modified_date.to_pydatetime()
            
            recipe_title = recipe.get('title', 'Unknown')
            # logger.info("Recipe '%s' last modified on %s", recipe_title, recipe_last_modified_date)
            
            # Skip if already processed and not forcing update.
            if existing_titles is not None and recipe_title in existing_titles:
                logger.info("Skipping recipe '%s' as it is already processed.", recipe_title)
                continue
            
            if recipe_last_modified_date > last_modified_date:
                logger.info("Processing recipe '%s'", recipe_title)
                try:
                    json_object = self.process_recipe(recipe)
                    # Manually attach lastmodifieddate from the source recipe.
                    json_object['lastmodifieddate'] = recipe_last_modified_date.isoformat()
                except Exception as e:
                    logger.error("Error processing recipe '%s': %s", recipe_title, e)
                    continue
                results.append(json_object)
            else:
                logger.info("Skipping recipe '%s' (last modified before cutoff)", recipe_title)
        
        return results

    def flatten_results(self, results):
        dfs = []
        for result in results:
            # Always include the title.
            meta = ['title']
            # Only include 'lastmodifieddate' if present.
            if 'lastmodifieddate' in result:
                meta.append('lastmodifieddate')
            try:
                df = json_normalize(result, record_path='ingredients', meta=meta, errors='ignore')
                dfs.append(df)
            except Exception as e:
                logger.error("Error flattening result for recipe '%s': %s", result.get('title', 'Unknown'), e)
        if dfs:
            return pd.concat(dfs, ignore_index=True)
        else:
            return pd.DataFrame()


class DatabaseManager:
    def __init__(self):
        self.user = os.getenv('DB_USER', 'postgres')
        self.password = os.getenv('DB_PASSWORD', 'postgres')
        self.host = os.getenv('DB_HOST', 'postgres')
        self.port = os.getenv('DB_PORT', '5432')
        self.db = os.getenv('DB_NAME', 'meal_planning')
        self.engine = create_engine(f'postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}')

    def wait_for_db(self, retries=10, delay=3):
        for i in range(retries):
            try:
                with self.engine.connect() as conn:
                    logger.info("Database connection established.")
                return True
            except OperationalError as e:
                logger.info("Database not ready (%d/%d), retrying in %d seconds...", i+1, retries, delay)
                time.sleep(delay)
        return False

    def get_recipes_from_db(self, schema, table):
        if not self.wait_for_db():
            raise Exception("Database is not ready after multiple attempts.")
        query = f"SELECT * FROM {schema}.{table}"
        recipes_df = pd.read_sql(query, self.engine)
        return recipes_df

    def get_processed_recipe_titles(self, schema='meal_planning', table='processed_recipes'):
        query = f"SELECT DISTINCT title FROM {schema}.{table}"
        with self.engine.connect() as conn:
            rows = conn.execute(text(query)).fetchall()
        return {row[0] for row in rows}

    def table_exists(self, table_name, schema='meal_planning'):
        query = f"""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = '{schema}' 
              AND table_name = '{table_name}'
        );
        """
        with self.engine.connect() as conn:
            exists = conn.execute(text(query)).scalar()
        return exists

    def write_to_db(self, table_name, df, schema='meal_planning', unique_constraint_columns=None):
        df.columns = [col.strip() for col in df.columns]
        if not self.table_exists(table_name, schema):
            logger.info("Table '%s.%s' does not exist. Please ensure it is created via init.sql or migrations.", schema, table_name)
            return

        columns = ', '.join([f'"{col}"' for col in df.columns])
        logger.info("Columns to be upserted: %s", columns)
        if unique_constraint_columns:
            conflict_clause = '(' + ', '.join([f'"{col}"' for col in unique_constraint_columns]) + ')'
            update_columns = ', '.join([f'"{col}" = EXCLUDED."{col}"' for col in df.columns if col not in unique_constraint_columns])
        else:
            conflict_clause = '("Title")'
            update_columns = ', '.join([f'"{col}" = EXCLUDED."{col}"' for col in df.columns if col != 'Title'])
        
        upsert_query = f"""
        INSERT INTO {schema}.{table_name} ({columns})
        VALUES %s
        ON CONFLICT {conflict_clause}
        DO UPDATE SET {update_columns};
        """
        
        values = [tuple(x) for x in df.to_numpy()]
        conn = psycopg2.connect(f"dbname={self.db} user={self.user} password={self.password} host={self.host} port={self.port}")
        cursor = conn.cursor()
        logger.info("Executing upsert query into processed_recipes table...")
        try:
            execute_values(cursor, upsert_query, values)
            conn.commit()
            logger.info("Table '%s.%s' updated successfully in database '%s'", schema, table_name, self.db)
        except Exception as e:
            logger.error("Error during upsert operation: %s", e)
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    def remove_deleted_recipes(self, processed_table, source_table, schema='meal_planning'):
        source_query = f"SELECT title FROM {schema}.{source_table}"
        with self.engine.connect() as conn:
            source_titles = {row[0] for row in conn.execute(text(source_query)).fetchall()}
        
        processed_query = f"SELECT DISTINCT title FROM {schema}.{processed_table}"
        with self.engine.connect() as conn:
            processed_titles = {row[0] for row in conn.execute(text(processed_query)).fetchall()}
        
        titles_to_delete = processed_titles - source_titles
        
        if titles_to_delete:
            placeholders = ", ".join([f"'{title}'" for title in titles_to_delete])
            delete_query = f"DELETE FROM {schema}.{processed_table} WHERE title IN ({placeholders})"
            with self.engine.connect() as conn:
                conn.execute(text(delete_query))
                conn.commit()
            logger.info("Deleted %d recipes from %s.%s that no longer exist in %s.%s.", len(titles_to_delete), schema, processed_table, schema, source_table)
        else:
            logger.info("No deleted recipes to remove.")


def main():
    db_manager = DatabaseManager()

    try:
        recipes_df = db_manager.get_recipes_from_db(schema='meal_planning', table='recipes')
        recipes_df['categories'] = recipes_df['categories'].str.split(',')
        logger.info("Fetched recipes from recipes table.")
    except Exception as e:
        logger.error("Error fetching recipes from the database: %s", e)
        return

    try:
        food_list_df = get_food_list()
        logger.info("Fetched food list from static text file.")
    except Exception as e:
        logger.error("Error reading food list: %s", e)
        return

    category_dict = food_list_df.set_index('Item')['Category'].to_dict()

    processor = RecipeProcessor(recipes_df, category_dict, INSTRUCTIONS, OUTPUT_FORMAT)
    
    # Convert LAST_MODIFIED_DATE from env into a timezone-aware datetime in GMT.
    gmt = timezone(timedelta(0), "GMT")
    last_modified_date = datetime.strptime(LAST_MODIFIED_DATE, "%Y-%m-%d").replace(tzinfo=gmt)
    # logger.info("Processing recipes modified after %s (GMT)", last_modified_date)
    
    # If we're not forcing an update, retrieve the set of already processed recipe titles.
    if not FORCE_UPDATE_PROCESSED:
        processed_titles = db_manager.get_processed_recipe_titles(schema='meal_planning', table='processed_recipes')
        logger.info("Skipping recipes already processed: %s", processed_titles)
    else:
        processed_titles = None
        logger.info("FORCE_UPDATE_PROCESSED enabled; processing all recipes with last modified date after %s", last_modified_date)
    
    results = processor.process_all_recipes(last_modified_date, num_recipes=NUM_RECIPES, existing_titles=processed_titles)

    if results:
        df_results = processor.flatten_results(results)
        
        # Deduplicate based on the unique key columns.
        df_results = df_results.drop_duplicates(subset=["title", "ingredient"])
        
        num_recipes = df_results['title'].nunique()
        num_ingredients = len(df_results)
        logger.info("Total processed recipes to upsert: %d", num_recipes)
        logger.info("Number of processed ingredients to upsert: %d", num_ingredients)        
        
        # Optionally, sort by lastmodifieddate for predictable upsert order.
        df_results = df_results.sort_values(by="lastmodifieddate", ascending=True)
        
        db_manager.write_to_db('processed_recipes', df_results, schema='meal_planning', unique_constraint_columns=["title", "ingredient"])
        db_manager.remove_deleted_recipes('processed_recipes', 'recipes', schema='meal_planning')
    else:
        logger.info("No recipes processed.")



if __name__ == '__main__':
    main()
