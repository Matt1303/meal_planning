import os
import time
import pandas as pd
from pandas import json_normalize
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from openai import OpenAI
import json
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import execute_values
from prompts import INSTRUCTIONS, OUTPUT_FORMAT

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

LAST_MODIFIED_DATE ='2025-01-29'
# LAST_MODIFIED_DATE = datetime.strptime(LAST_MODIFIED_DATE_STR, "%Y-%m-%d").replace(tzinfo=timezone.utc)
NUM_RECIPES=10

# LAST_MODIFIED_DATE = os.getenv("LAST_MODIFIED_DATE")
# NUM_RECIPES = os.getenv("NUM_RECIPES")


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
        self.recipes_df = recipes_df
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
            print(f"Error getting response from OpenAI: {e}")
            return None    

    def process_recipe(self, recipe):
        dictionaries = f"ingredients_dict = {recipe}\ncategory_dict = {self.category_dict}"
        prompt = self.instructions + self.output_format + f"\n\nHere are the input dictionaries:\n```{dictionaries}```"
        
        response = self.get_response(prompt)
        if response is None:
            raise ValueError("No response received from OpenAI.")

        # Remove markdown delimiters if present
        if response.startswith('```json'):
            response = response[7:-3].strip()

        try:
            json_object = json.loads(response)
        except json.JSONDecodeError as e:
            print("Error parsing JSON from OpenAI response:", e)
            print("Response was:", response)
            json_object = None

        return json_object
    
    def process_all_recipes(self, last_modified_date, num_recipes=None):
        results = []

        # Convert the input last_modified_date to a datetime object
        last_modified_date = datetime.strptime(last_modified_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)

        # Determine the number of recipes to process
        if num_recipes is None:
            num_recipes = len(self.recipes_df)

        # Loop through each row in the DataFrame
        for i in range(num_recipes):
            # Extract the row as a dictionary
            recipe = self.recipes_df.iloc[i].to_dict()

            # Filter recipes based on the last modified date
            recipe_last_modified_date = recipe['lastmodifieddate']
            if isinstance(recipe_last_modified_date, pd.Timestamp):
                recipe_last_modified_date = recipe_last_modified_date.to_pydatetime()
            if recipe_last_modified_date > last_modified_date:
                # Process the recipe
                json_object = self.process_recipe(recipe)

                # Append the JSON object to the results list
                results.append(json_object)

        return results

    def flatten_results(self, results):
        # Flatten the JSON structure and create a DataFrame
        df = pd.concat([json_normalize(result, 'ingredients', ['title']) for result in results], ignore_index=True)
        return df
    


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
                    print("Database connection established.")
                return True
            except OperationalError as e:
                print(f"Database not ready ({i+1}/{retries}), retrying in {delay} seconds...")
                time.sleep(delay)
        return False

    def get_recipes_from_db(self, schema, table):
        if not self.wait_for_db():
            raise Exception("Database is not ready after multiple attempts.")
        query = f"SELECT * FROM {schema}.{table}"
        recipes_df = pd.read_sql(query, self.engine)
        return recipes_df

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
        """
        Upserts the DataFrame into the specified table.
        Assumes that the table already exists (as created by init.sql).
        If unique_constraint_columns is provided (list of columns), they are used in the ON CONFLICT clause.
        """
        # Normalize column names.
        df.columns = [col.strip() for col in df.columns]

        if not self.table_exists(table_name, schema):
            print(f"Table '{schema}.{table_name}' does not exist. Please ensure it is created via init.sql or migrations.")
            return

        # Prepare column names.
        columns = ', '.join([f'"{col}"' for col in df.columns])
        print(f"Columns to be upserted: {columns}")
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
        print("Executing upsert query into processed_recipes table...")
        try:
            execute_values(cursor, upsert_query, values)
            conn.commit()
            print(f"Table '{schema}.{table_name}' updated successfully in database '{self.db}'")
        except Exception as e:
            print("Error during upsert operation:", e)
            conn.rollback()
        finally:
            cursor.close()
            conn.close()


def main():
    # Instantiate the DatabaseManager; it will pick up connection info from environment variables.
    db_manager = DatabaseManager()

    # Retrieve recipes from the 'meal_planning' schema and 'recipes' table.
    try:
        recipes_df = db_manager.get_recipes_from_db(schema='meal_planning', table='recipes')
        # Split the Categories column on commas (if it contains comma-separated values)
        recipes_df['categories'] = recipes_df['categories'].str.split(',')        
        print("Fetched recipes:")
        print(recipes_df.head())
    except Exception as e:
        print(f"Error fetching recipes from the database: {e}")
        return        

    # Retrieve and process the food list.
    try:
        food_list_df = get_food_list()
        print("Fetched food list:")
        print(food_list_df.head())
    except Exception as e:
        print(f"Error reading food list: {e}")
        return

    # Convert data to dictionaries as required by RecipeProcessor.
    # For example, you might want a dictionary mapping recipe titles to their Ingredients and Servings.
    # Here, we'll assume that recipes_df has columns 'Title', 'Ingredients', and 'Servings'.
    
    # You can create a dictionary like this:
    # ingredients_dict = recipes_df.set_index('Title')[['Ingredients', 'Servings']].to_dict('index')
    
    # Similarly, convert your food list DataFrame to a dictionary.
    # Suppose your food list DataFrame has columns 'Item' and 'Category'.
    category_dict = food_list_df.set_index('Item')['Category'].to_dict()
    
    # Instantiate the RecipeProcessor with your instructions and output format.
    processor = RecipeProcessor(recipes_df, category_dict, INSTRUCTIONS, OUTPUT_FORMAT)   
    
    # Process recipes and flatten the results.
    results = processor.process_all_recipes(LAST_MODIFIED_DATE, num_recipes=NUM_RECIPES)

    if results:
        df_results = processor.flatten_results(results)
        print("Processed results:")
        print(df_results)
        # Upsert the processed results into a new table with a unique constraint on Title and Ingredient.
        db_manager.write_to_db('processed_recipes', df_results, schema='meal_planning', unique_constraint_columns=["title", "ingredient"])
    else:
        print("No recipes processed.")

if __name__ == '__main__':
    main()
