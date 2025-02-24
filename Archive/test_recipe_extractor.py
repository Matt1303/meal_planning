import os
import unittest
from bs4 import BeautifulSoup
from unittest.mock import patch, mock_open
from ingest_data_meal_planning import RecipeExtractor

# FILE: test_recipe_extractor.py


class TestRecipeExtractor(unittest.TestCase):

    @patch('os.listdir', return_value=['test.html'])
    @patch('builtins.open', new_callable=mock_open, read_data='<html><h1>Test Recipe</h1><div class="ingredients text">1 cup sugar</div><p class="categories">Dessert</p></html>')
    def test_parse_html_files(self, mock_open, mock_listdir):
        extractor = RecipeExtractor('test_dir')
        extractor.parse_html_files()
        self.assertEqual(len(extractor.recipes), 1)
        self.assertEqual(extractor.recipes[0]['Title'], 'Test Recipe')
        self.assertEqual(extractor.recipes[0]['Ingredients'], ['1 cup sugar'])
        self.assertEqual(extractor.recipes[0]['Categories'], 'Dessert')

    def test_extract_data(self):
        html_content = '<html><h1>Test Recipe</h1><div class="ingredients text">1 cup sugar</div><p class="categories">Dessert</p></html>'
        soup = BeautifulSoup(html_content, 'html.parser')
        extractor = RecipeExtractor('test_dir')
        title, ingredients_text, category = extractor.extract_data(soup)
        self.assertEqual(title, 'Test Recipe')
        self.assertEqual(ingredients_text, '1 cup sugar')
        self.assertEqual(category, 'Dessert')

    def test_split_ingredients(self):
        ingredients_text = '1 cup sugar 2 cups flour'
        extractor = RecipeExtractor('test_dir')
        ingredients_list = extractor.split_ingredients(ingredients_text)
        self.assertEqual(ingredients_list, ['1 cup sugar', '2 cups flour'])

    def test_merge_ingredients(self):
        ingredients_list = ['1', 'cup', 'sugar', '2', 'cups', 'flour']
        extractor = RecipeExtractor('test_dir')
        merged_ingredients = extractor.merge_ingredients(ingredients_list)
        self.assertEqual(merged_ingredients, ['1 cup', 'sugar', '2 cups', 'flour'])

    def test_finalize_ingredients(self):
        merged_ingredients = ['0.', '25 cups', '0.', '5 cups']
        extractor = RecipeExtractor('test_dir')
        final_ingredients = extractor.finalize_ingredients(merged_ingredients)
        self.assertEqual(final_ingredients, ['0.25 cups', '0.5 cups'])

    def test_filter_ingredients(self):
        final_ingredients = ['1 cup sugar', '2 cups Flour']
        extractor = RecipeExtractor('test_dir')
        filtered_ingredients = extractor.filter_ingredients(final_ingredients)
        self.assertEqual(filtered_ingredients, ['1 cup sugar'])

if __name__ == '__main__':
    unittest.main()