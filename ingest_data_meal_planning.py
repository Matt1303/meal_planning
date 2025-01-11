#!/usr/bin/env python

# coding: utf-8

import os
import time
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from git import Repo
import glob
from bs4 import BeautifulSoup
import re

class RecipeExtractor:
    def __init__(self, html_dir):
        self.html_dir = html_dir
        self.recipes = []

    def parse_html_files(self):
        for filename in os.listdir(self.html_dir):
            if filename.endswith('.html'):
                with open(os.path.join(self.html_dir, filename), 'r', encoding='utf-8') as file:
                    soup = BeautifulSoup(file, 'html.parser')
                    title, ingredients_text, category = self.extract_data(soup)
                    ingredients_list = self.split_ingredients(ingredients_text)
                    merged_ingredients = self.merge_ingredients(ingredients_list)
                    final_ingredients = self.finalize_ingredients(merged_ingredients)
                    filtered_ingredients = self.filter_ingredients(final_ingredients)
                    self.store_data(title, filtered_ingredients, category)

    def extract_data(self, soup):
        title = soup.find('h1').text
        ingredients_text = soup.find('div', class_='ingredients text').text
        category = soup.find('p', class_='categories').text
        return title, ingredients_text, category


    def split_ingredients(self, ingredients_text):
        ingredients_list = re.split(r'(?=\d+\.\d+\s[a-zA-Z])|(?=\d+\s[a-zA-Z])|(?=One\s[a-zA-Z])|(?=Two\s[a-zA-Z])|(?=Three\s[a-zA-Z])|(?=Four\s[a-zA-Z])|(?=Five\s[a-zA-Z])|(?<=[a-z])(?=[A-Z])', ingredients_text)
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
            if merged_ingredients[i] == '0.' and i + 1 < len(merged_ingredients):
                if re.match(r'^25', merged_ingredients[i + 1]):
                    final_ingredients.append('0.25' + merged_ingredients[i + 1][2:])
                    i += 2

                elif re.match(r'^5', merged_ingredients[i + 1]):
                    final_ingredients.append('0.5' + merged_ingredients[i + 1][1:])
                    i += 2

                else:
                    final_ingredients.append(merged_ingredients[i])
                    i += 1

            else:
                final_ingredients.append(merged_ingredients[i])
                i += 1

        final_ingredients_with_prepend = []

        i = 0

        while i < len(final_ingredients):
            if final_ingredients[i] == '1 to' and i + 1 < len(final_ingredients):
                final_ingredients_with_prepend.append('1 to ' + final_ingredients[i + 1])
                i += 2

            else:
                final_ingredients_with_prepend.append(final_ingredients[i])
                i += 1

        return final_ingredients_with_prepend


    def filter_ingredients(self, final_ingredients):
        return [ingredient for ingredient in final_ingredients if not re.match(r'^([A-Z][a-z]*\s*)+$', ingredient)]


    def store_data(self, title, ingredients, category):
        self.recipes.append({
            "Title": title,
            "Ingredients": ingredients,
            "Categories": category
        })


    def store_all_recipes(self):

        recipes_dict = {recipe["Title"]: {"Ingredients": recipe["Ingredients"], "Categories": recipe["Categories"]} for recipe in self.recipes}
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

        except OperationalError:
            print("Database not ready, waiting...")
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
    clone_repo(repo_url, clone_dir)

    # Initialize RecipeExtractor with the folder containing HTML files
    extractor = RecipeExtractor(clone_dir)
    extractor.parse_html_files()
    recipes_dict = extractor.store_all_recipes()
    recipes_df = pd.DataFrame(recipes_dict).T.reset_index().rename(columns={'index': 'Title'})

    # Print the DataFrame to check its content
    print(recipes_df.head())

    # Create a connection to the PostgreSQL database
    engine = create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')

    # Wait for the database to be ready
    if not wait_for_db(engine):
        print("Database connection failed after retries")
        return

    # Load the DataFrame into the database, replacing the table if it already exists
    recipes_df.to_sql(name=table_name, con=engine, if_exists='replace', index=False)
    print(f"Table '{table_name}' created successfully in database '{db}'")

if __name__ == '__main__':
    main()