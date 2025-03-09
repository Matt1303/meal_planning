
import os
import pandas as pd
from pyomo.environ import *
from db_manager import DatabaseManager
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

def main():
    # Instantiate your shared DatabaseManager
    db_manager = DatabaseManager()
    # Retrieve data from the processed_recipes table, for example.
    ingredients = pd.read_sql("SELECT * FROM meal_planning.processed_recipes", db_manager.engine, columns=['title', 'ingredient', 'serving_quantity', 'category'])
    logger.info("Fetched ingredients info: %d rows", len(ingredients))

    meals = pd.read_sql("SELECT * FROM meal_planning.recipes", db_manager.engine, columns=['title', 'categories', 'rating', 'difficulty'])
    logger.info("Fetched meal info: %d rows", len(meals))

    # create new column 'meal_type' in meals dataframe by looking for 'Breakfast', 'Lunch', 'Dinner', 'Snacks' in 'categories' column
    meals['meal_type'] = meals['categories'].apply(lambda x: [meal_type for meal_type in ['Breakfast', 'Lunch', 'Dinner', 'Snacks'] if meal_type in x])
    # drop 'categories' column
    meals.drop(columns=['categories'], inplace=True)

    # Merge the two DataFrames on the 'title' column.
    recipe_data = pd.merge(meals, ingredients, on='title')
    logger.info("Merged data: %d rows", len(recipe_data))


    
    # Build your Pyomo model using data from df.
    # For instance, define sets, parameters, decision variables, constraints, and objective.
    # (The exact model will depend on your specific meal planning constraints.)
    
    
    # You can also write the meal plan back to the database if needed.
    
if __name__ == '__main__':
    main()
