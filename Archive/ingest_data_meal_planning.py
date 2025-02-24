#!/usr/bin/env python

# coding: utf-8

import os
import time
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from git import Repo, Git
import glob
from bs4 import BeautifulSoup
import re
from datetime import datetime

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
                    ingredients_list = self.split_ingredients(ingredients_text)
                    merged_ingredients = self.merge_ingredients(ingredients_list)
                    final_ingredients = self.finalize_ingredients(merged_ingredients)
                    filtered_ingredients = self.filter_ingredients(final_ingredients)
                    last_modified_date = self.get_last_modified_date(filename)
                    self.store_data(title, filtered_ingredients, category, rating, servings, difficulty, last_modified_date)
        
    def extract_data(self, soup):
        title = soup.find('h1').text if soup.find('h1') else ''
        ingredients_text = soup.find('div', class_='ingredients text').text if soup.find('div', class_='ingredients text') else ''
        category_tag = soup.find('p', class_='categories')
        category = category_tag.text if category_tag else ''
        rating_tag = soup.find('p', class_='rating')
        rating = rating_tag['value'] if rating_tag else ''
        servings_tag = soup.find('span', itemprop='recipeYield')
        servings = servings_tag.text if servings_tag else ''
        difficulty_tag = soup.find('span', itemprop='difficulty')
        difficulty = difficulty_tag.text if difficulty_tag else ''
        return title, ingredients_text, category, rating, servings, difficulty

    # def split_ingredients(self, ingredients_text):
    #     ingredients_list = re.split(r'(?=\d+\.\d+\s[a-zA-Z])|(?=\d+\s[a-zA-Z])|(?=One\s[a-zA-Z])|(?=Two\s[a-zA-Z])|(?=Three\s[a-zA-Z])|(?=Four\s[a-zA-Z])|(?=Five\s[a-zA-Z])|(?<=[a-z])(?=[A-Z])', ingredients_text)
    #     # pattern = (
    #     # r'(?=\b\d+(?!\.\d)\s[a-zA-Z])'
    #     # r'|(?=One\s[a-zA-Z])'
    #     # r'|(?=Two\s[a-zA-Z])'
    #     # r'|(?=Three\s[a-zA-Z])'
    #     # r'|(?=Four\s[a-zA-Z])'
    #     # r'|(?=Five\s[a-zA-Z])'
    #     # r'|(?<=[a-z])(?=[A-Z])'
    #     # )
    #     # ingredients_list = re.split(pattern, ingredients_text)
    #     return [ingredient.strip() for ingredient in ingredients_list if ingredient.strip()]

    def split_ingredients(self, ingredients_text):
        ingredients_list = re.split(
            r'(?=\d+(?!\.\d)\s[a-zA-Z])'
            r'|(?=One\s[a-zA-Z])'
            r'|(?=Two\s[a-zA-Z])'
            r'|(?=Three\s[a-zA-Z])'
            r'|(?=Four\s[a-zA-Z])'
            r'|(?=Five\s[a-zA-Z])'
            r'|(?<=[a-z])(?=[A-Z])',
            ingredients_text
        )
        return [ingredient.strip() for ingredient in ingredients_list if ingredient.strip()]


    def merge_ingredients(self, ingredients_list):
        merged_ingredients = []
        i = 0
        while i < len(ingredients_list):
            if re.match(r'^\d+(\.\d+)?$', ingredients_list[i]):
                number = ingredients_list[i]
                while i + 1 < len(ingredients_list) and re.match(r'^\d+(\.\d+)?$', ingredients_list[i + 1]):
                    number += ingredients_list[i + 1]
                    i += 1
                if i + 1 < len(ingredients_list):
                    merged_ingredients.append(number + '' + ingredients_list[i + 1])
                    i += 2
                else:
                    merged_ingredients.append(number)
                    i += 1
            elif re.match(r'^0\.\d$', ingredients_list[i]):
                if i + 1 < len(ingredients_list) and re.match(r'^\d$', ingredients_list[i + 1]):
                    merged_ingredients.append(ingredients_list[i] + ingredients_list[i + 1])
                    i += 2
                else:
                    merged_ingredients.append(ingredients_list[i])
                    i += 1
            else:
                merged_ingredients.append(ingredients_list[i])
                i += 1
        return merged_ingredients

    def finalize_ingredients(self, merged_ingredients):
        final_ingredients = []
        i = 0
        while i < len(merged_ingredients):
            # Normalize the current token by stripping whitespace and trailing commas.
            token_current = merged_ingredients[i].strip().rstrip(',')
            
            if token_current == '0.' and i + 1 < len(merged_ingredients):
                token_next = merged_ingredients[i + 1].strip().rstrip(',')
                if re.match(r'^25', token_next):
                    # Merge "0." and the following token starting with "25" into "0.25..."
                    merged = '0.25' + token_next[2:]
                    final_ingredients.append(merged)
                    i += 2
                elif re.match(r'^5', token_next):
                    # Merge into "0.5..."
                    merged = '0.5' + token_next[1:]
                    final_ingredients.append(merged)
                    i += 2
                elif re.match(r'^67', token_next):
                    # Merge into "0.67..."
                    merged = '0.67' + token_next[2:]
                    final_ingredients.append(merged)
                    i += 2
                else:
                    final_ingredients.append(token_current)
                    i += 1
            elif token_current == '1.' and i + 1 < len(merged_ingredients):
                token_next = merged_ingredients[i + 1].strip().rstrip(',')
                if re.match(r'^2', token_next):
                    merged = '1.2' + token_next[1:]
                    final_ingredients.append(merged)
                    i += 2
                elif re.match(r'^5', token_next):
                    merged = '1.5' + token_next[1:]
                    final_ingredients.append(merged)
                    i += 2
                else:
                    final_ingredients.append(token_current)
                    i += 1
            else:
                final_ingredients.append(token_current)
                i += 1

        final_ingredients_with_prepend = []
        i = 0
        while i < len(final_ingredients):
            token = final_ingredients[i].strip()
            if token == '1 to' and i + 1 < len(final_ingredients):
                final_ingredients_with_prepend.append('1 to ' + final_ingredients[i + 1].strip())
                i += 2
            else:
                final_ingredients_with_prepend.append(token)
                i += 1
        return final_ingredients_with_prepend



    def filter_ingredients(self, final_ingredients):
        return [ingredient for ingredient in final_ingredients if not re.match(r'^([A-Z][a-z]*\s*)+$', ingredient)]

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
        recipes_dict = {recipe["Title"]: {"Ingredients": recipe["Ingredients"], "Categories": recipe["Categories"], "Rating": recipe["Rating"], "Servings": recipe["Servings"], "Difficulty": recipe["Difficulty"], "LastModifiedDate": recipe["LastModifiedDate"]} for recipe in self.recipes}
        return recipes_dict

def clone_repo(repo_url, clone_dir):
    if os.path.exists(clone_dir):
        print(f"Removing existing directory: {clone_dir}")
        os.system(f'rm -rf {clone_dir}')
    print(f"Cloning repository {repo_url} into {clone_dir}")
    Repo.clone_from(repo_url, clone_dir)
    print(f"Repository cloned into {clone_dir}")

def wait_for_db(engine, retries=5, delay=5):
    for _ in range(retries):
        try:
            with engine.connect() as conn:
                return True
        except OperationalError as e:
            print(f"Database not ready, error: {str(e)}")
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

    # Print the database connection details
    print(f"Connecting to database with the following details:")
    print(f"User: {user}")
    print(f"Password: {password}")
    print(f"Host: {host}")
    print(f"Port: {port}")
    print(f"Database: {db}")

    # Clone the GitHub repository
    clone_repo(repo_url, clone_dir)

    # Initialize RecipeExtractor with the folder containing HTML files
    repo = Repo(clone_dir)
    extractor = RecipeExtractor(clone_dir, repo)
    extractor.parse_html_files()
    recipes_dict = extractor.store_all_recipes()
    recipes_df = pd.DataFrame(recipes_dict).T.reset_index().rename(columns={'index': 'Title'})
    
    # Rename the columns to match the database table
    recipes_df.columns = [col.lower() for col in recipes_df.columns]   

    # Print the DataFrame to check its content
    print(recipes_df.sort_values(by='lastmodifieddate').tail())     

    # Create a connection to the PostgreSQL database
    engine = create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')

    # Wait for the database to be ready
    if not wait_for_db(engine):
        print("Database connection failed after retries")
        return

    # # Load the DataFrame into the database, replacing the table if it already exists
    # recipes_df.to_sql(name=table_name, con=engine, if_exists='replace', index=False)
    # print(f"Table '{table_name}' created successfully in database '{db}'")

    # # Load the DataFrame into the database, appending the data to the existing table
    # recipes_df.to_sql(name=table_name, con=engine, if_exists='append', index=False, schema='meal_planning')
    # print(f"Data appended successfully to table '{table_name}' in schema 'meal_planning' of database '{db}'") 

    # Upsert data into the database
    with engine.begin() as connection:
        for index, row in recipes_df.iterrows():
            # print(f"Upserting row: {row.to_dict()}")  # Debug print
            ingredients_str = ', '.join(row['ingredients'])  # Convert list to string
            categories_str = ', '.join(row['categories']) if isinstance(row['categories'], list) else row['categories']  # 
            
            # Build parameters as a dictionary with named keys
            params = {
                'title': row['title'],
                'ingredients': ingredients_str,
                'categories': categories_str,
                'rating': row['rating'],
                'servings': row['servings'],
                'difficulty': row['difficulty'],
                'lastmodifieddate': row['lastmodifieddate']
            }

            # Create your SQL statement with named placeholders
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

            # Execute the query with the dictionary of parameters
            connection.execute(insert_query, params)

    print(f"Data upserted successfully into table '{table_name}' in database '{db}'") 

if __name__ == '__main__':
    main()