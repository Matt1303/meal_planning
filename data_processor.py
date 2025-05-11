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
from db_manager import DatabaseManager

# Constants for meal types
MEAL_TYPES = ['Breakfasts', 'Lunches', 'Dinner', 'Snacks']

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Environment Defaults
default_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
LAST_MODIFIED_DATE = os.getenv("LAST_MODIFIED_DATE", default_date) or default_date
NUM_RECIPES = os.getenv("NUM_RECIPES")
NUM_RECIPES = int(NUM_RECIPES) if NUM_RECIPES and NUM_RECIPES.isdigit() else None
FORCE_UPDATE_PROCESSED = os.getenv("FORCE_UPDATE_PROCESSED", "True").lower() in ["true","1","yes"]

# Log environment settings
logger.info("LAST_MODIFIED_DATE: %s", LAST_MODIFIED_DATE)
logger.info("NUM_RECIPES: %s", NUM_RECIPES or 'all')
logger.info("FORCE_UPDATE_PROCESSED: %s", FORCE_UPDATE_PROCESSED)


def get_food_list():
    file_path = os.getenv("FOOD_LIST_PATH")
    if not file_path:
        raise ValueError("FOOD_LIST_PATH not set")
    with open(file_path) as f:
        lines = f.readlines()
    data, current = [], None
    for i, ln in enumerate(lines):
        ln = ln.strip()
        if ln:
            if current is None and (i==0 or not lines[i-1].strip()) and (i+1<len(lines) and not lines[i+1].strip()):
                current = ln; data.append({'Category':ln,'Item':None})
            else:
                data.append({'Category':None,'Item':ln})
        else:
            current = None
    df = pd.DataFrame(data)
    df['Category'] = df['Category'].ffill()
    return df.dropna(subset=['Item'])

class RecipeProcessor:
    def __init__(self, recipes_df, category_dict, instructions, output_format):
        self.recipes_df = recipes_df.sort_values(by='lastmodifieddate')
        self.category_dict = category_dict
        self.instructions = instructions
        self.output_format = output_format

    def get_response(self, prompt, temperature=0.0):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role":"user","content":prompt}],
                temperature=temperature
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error("OpenAI error: %s", e)
            return None

    def process_recipe(self, recipe):
        dicts = f"ingredients_dict = {recipe}\ncategory_dict = {self.category_dict}"
        prompt = self.instructions + self.output_format + f"\n\nHere are the input dicts:\n```{dicts}```"
        raw = self.get_response(prompt)
        if not raw:
            raise RuntimeError("No response from OpenAI")
        if raw.startswith('```json'):
            raw = raw[7:-3].strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s", e)
            obj = None
        return obj

    def process_all_recipes(self, cutoff, limit=None, existing=None):
        if isinstance(cutoff, str):
            cutoff = datetime.strptime(cutoff, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        logger.info("Cutoff date: %s", cutoff)
        n = limit or len(self.recipes_df)
        res = []
        for _, row in self.recipes_df.head(n).iterrows():
            title = row['title']; lm = row['lastmodifieddate']
            if isinstance(lm, pd.Timestamp): lm = lm.to_pydatetime()
            if existing and title in existing and not FORCE_UPDATE_PROCESSED:
                continue
            if lm > cutoff:
                logger.info("Processing '%s' (modified %s)", title, lm)
                obj = self.process_recipe(row.to_dict())
                if obj:
                    obj['lastmodifieddate'] = lm.isoformat()
                    res.append(obj)
        return res

    def flatten_results(self, results):
        dfs = []
        for obj in results:
            meta = ['title','lastmodifieddate']
            try:
                df = json_normalize(obj, 'ingredients', meta=meta, errors='ignore')
                dfs.append(df)
            except Exception as e:
                logger.error("Flatten error for %s: %s", obj.get('title'), e)
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def write_processed_with_mealtypes(df, recipes_df, db_manager):
    # One-hot encode meal types based on source recipes_df
    recipes_df['categories'] = recipes_df['categories'].apply(lambda x: x if isinstance(x,list) else x.split(','))
    for m in MEAL_TYPES:
        recipes_df[m] = recipes_df['categories'].apply(lambda cats: 1 if m in cats else 0)
    # Merge flags into processed df
    merged = df.merge(recipes_df[['title']+MEAL_TYPES], on='title', how='left')
    merged.rename(columns={m: m.lower() for m in MEAL_TYPES}, inplace=True)
    # Upsert into Postgres
    db_manager.write_to_db('processed_recipes', merged, schema='meal_planning',
                           unique_constraint_columns=["title","ingredient"])
    return merged


def main():
    db = DatabaseManager()
    # Load source recipes
    recipes_df = db.get_recipes_from_db('meal_planning','recipes')
    recipes_df['categories'] = recipes_df['categories'].str.split(',')
    # Optional: load existing processed titles
    existing = None if FORCE_UPDATE_PROCESSED else db.get_processed_recipe_titles('meal_planning','processed_recipes')
    # Process
    proc = RecipeProcessor(recipes_df, get_food_list().set_index('Item')['Category'].to_dict(), INSTRUCTIONS, OUTPUT_FORMAT)
    results = proc.process_all_recipes(LAST_MODIFIED_DATE, NUM_RECIPES, existing)
    if not results:
        logger.info("No new recipes to process")
        return
    df_res = proc.flatten_results(results).drop_duplicates(subset=['title','ingredient'])
    logger.info("Upserting %d ingredients for %d recipes", df_res.shape[0], df_res['title'].nunique())
    # Merge one-hot & write
    merged = write_processed_with_mealtypes(df_res, recipes_df, db)
    db.remove_deleted_recipes('processed_recipes','recipes','meal_planning')

if __name__ == '__main__':
    main()
