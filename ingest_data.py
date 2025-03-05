#!/usr/bin/env python
# coding: utf-8

import os
import time
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from git import Repo, Git
from bs4 import BeautifulSoup
# import re
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Use DEBUG for more detailed output
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class RecipeExtractor:
    def __init__(self, html_dir, repo):
        self.html_dir = html_dir
        self.repo = repo
        self.recipes = []

    def parse_html_files(self):
        for filename in os.listdir(self.html_dir):
            if filename.endswith('.html'):
                with open(os.path.join(self.html_dir, filename), 'r', encoding='utf-8') as file:
                    soup = BeautifulSoup(file, 'html.parser')
                    title, ingredients_text, category, rating, servings, difficulty = self.extract_data(soup)
                    # Process ingredients: normalize spaces and (if needed) further cleanup.
                    processed_ingredients = self.process_ingredients(ingredients_text)
                    last_modified_date = self.get_last_modified_date(filename)
                    self.store_data(title, processed_ingredients, category, rating, servings, difficulty, last_modified_date)
        
    def extract_data(self, soup):
        # Extract title
        title = soup.find('h1').text if soup.find('h1') else ''
        
        # For ingredients, look for the container and then find all <p class="line" ...> tags.
        ingredients_container = soup.find('div', class_='ingredients text')
        if ingredients_container:
            # Find all paragraphs that represent an ingredient.
            ingredient_paragraphs = ingredients_container.find_all('p', class_='line')
            # Get each paragraph's text (using a space as a separator in case there are inline elements)
            # and join them with a semicolon and a space.
            ingredients_text = "; ".join(p.get_text(separator=" ", strip=True) for p in ingredient_paragraphs)
        else:
            ingredients_text = ''
        
        # Extract category
        category_tag = soup.find('p', class_='categories')
        category = category_tag.text if category_tag else ''
        # Extract rating
        rating_tag = soup.find('p', class_='rating')
        rating = rating_tag['value'] if rating_tag else ''
        # Extract servings
        servings_tag = soup.find('span', itemprop='recipeYield')
        servings = servings_tag.text if servings_tag else ''
        # Extract difficulty
        difficulty_tag = soup.find('span', itemprop='difficulty')
        difficulty = difficulty_tag.text if difficulty_tag else ''
        return title, ingredients_text, category, rating, servings, difficulty

    def process_ingredients(self, ingredients_text):
        """
        Further normalize the ingredients text by replacing any multiple spaces
        with a single space. (Since our extraction already joins the individual
        ingredients with a semicolon, we don't need to worry about inserting extra delimiters.)
        """
        return ' '.join(ingredients_text.split())

    def get_last_modified_date(self, filename):
        git = Git(self.html_dir)
        log_info = git.log('-1', '--format=%cd', '--', filename)
        last_modified_date = datetime.strptime(log_info.strip(), '%a %b %d %H:%M:%S %Y %z')
        return last_modified_date

    def store_data(self, title, ingredients, category, rating, servings, difficulty, last_modified_date):
        self.recipes.append({
            "Title": title,
            "Ingredients": ingredients,
            "Categories": category,
            "Rating": rating,
            "Servings": servings,
            "Difficulty": difficulty,
            "LastModifiedDate": last_modified_date
        })

    def store_all_recipes(self):
        recipes_dict = {
            recipe["Title"]: {
                "Ingredients": recipe["Ingredients"],
                "Categories": recipe["Categories"],
                "Rating": recipe["Rating"],
                "Servings": recipe["Servings"],
                "Difficulty": recipe["Difficulty"],
                "LastModifiedDate": recipe["LastModifiedDate"]
            }
            for recipe in self.recipes
        }
        return recipes_dict

def clone_repo(repo_url, clone_dir):
    if os.path.exists(clone_dir):
        logger.info(f"Removing existing directory: {clone_dir}")
        os.system(f'rm -rf {clone_dir}')
    logger.info(f"Cloning repository {repo_url} into {clone_dir}")
    Repo.clone_from(repo_url, clone_dir)
    logger.info(f"Repository cloned into {clone_dir}")

def wait_for_db(engine, retries=5, delay=5):
    for _ in range(retries):
        try:
            with engine.connect() as conn:
                return True
        except OperationalError as e:
            logger.error(f"Database not ready, error: {str(e)}")
            time.sleep(delay)
    return False

def main():
    user = os.getenv('DB_USER')
    password = os.getenv('DB_PASSWORD')
    host = os.getenv('DB_HOST')
    port = os.getenv('DB_PORT')
    db = os.getenv('DB_NAME')
    table_name = os.getenv('TABLE_NAME')
    repo_url = os.getenv('REPO_URL')
    clone_dir = os.getenv('CLONE_DIR')

    # Clone the GitHub repository
    logger.info(f"Cloning repository {repo_url} into {clone_dir}")
    clone_repo(repo_url, clone_dir)

    # Initialize RecipeExtractor with the folder containing HTML files
    repo = Repo(clone_dir)
    extractor = RecipeExtractor(clone_dir, repo)
    extractor.parse_html_files()
    recipes_dict = extractor.store_all_recipes()
    recipes_df = pd.DataFrame(recipes_dict).T.reset_index().rename(columns={'index': 'Title'})
    
    # Rename the columns to match the database table
    recipes_df.columns = [col.lower() for col in recipes_df.columns]   

    # Create a connection to the PostgreSQL database
    engine = create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')

    # Wait for the database to be ready
    if not wait_for_db(engine):
        logger.info("Database connection failed after retries")
        return

    # Upsert data into the database
    with engine.begin() as connection:
        for index, row in recipes_df.iterrows():
            # Use the processed ingredients string as is.
            ingredients_str = row['ingredients']
            # Categories is assumed to be a string.
            categories_str = row['categories']
            
            params = {
                'title': row['title'],
                'ingredients': ingredients_str,
                'categories': categories_str,
                'rating': row['rating'],
                'servings': row['servings'],
                'difficulty': row['difficulty'],
                'lastmodifieddate': row['lastmodifieddate']
            }

            insert_query = text(f"""
                INSERT INTO meal_planning.{table_name} (title, ingredients, categories, rating, servings, difficulty, lastmodifieddate)
                VALUES (:title, :ingredients, :categories, :rating, :servings, :difficulty, :lastmodifieddate)
                ON CONFLICT (title) DO UPDATE SET
                    ingredients = EXCLUDED.ingredients,
                    categories = EXCLUDED.categories,
                    rating = EXCLUDED.rating,
                    servings = EXCLUDED.servings,
                    difficulty = EXCLUDED.difficulty,
                    lastmodifieddate = EXCLUDED.lastmodifieddate;
            """)
            connection.execute(insert_query, params)

    logger.info(f"Data upserted successfully into table '{table_name}' in database '{db}'")

if __name__ == '__main__':
    main()
