# Define the instructions
INSTRUCTIONS = """You will be provided with information containing the following keys: 'Title', 'Ingredients' and 'Servings'.
The 'Ingredients' key contains a list of strings, each representing an ingredient and its quantity (likely in grams/millilitre), and possibly some further prepping instruction in a recipe (which we don't need to use). The 'Servings' key represents the number of servings of the meal that the set of ingredients will produce.
You will also be provided with information containing the following keys: 'Category' and 'Item'.
The 'Items' represent a food item and the 'Category' represents the food category it belongs to. Use this, along with your expert knowledge of plant foods, as your basis to categorise each plant-based ingredient you find. Please only store ingredients that fit into these ten distinct categories (Beans, Berries, Other Fruits, Cruciferous Vegetables, Greens, Other Vegetables, Flaxseeds or Linseeds, Nuts and Seeds, Herbs and Spices, Whole Grains).
Any oils, milks and pastes should not be included. Only fresh herbs and spices should be included, no dried. Therefore do not include any herbs or spices that are described as 'ground'.
Moreover, use the quantity of each of the ingredients you identify along with the Servings information to calculate the total amount of that food per serving, using the relevant units (e.g. grams, ml, tbsp). Given that the input data is a string and has not been cleaned/converted to numeric, you may encounter servings sizes that e.g. give a range or have other ambiguity. Do your best to calculate the amounts per serving given your understanding and knowledge."""

# Define the output format
OUTPUT_FORMAT = """The output should only be a JSON object in the following format, do not include any additional text or explanation of your logic.
{
    "title": "Nothing Fishy Sushi Wraps",
    "ingredients": [
        {
            "ingredient": "cashews",
            "serving_quantity": "15 grams",
            "category": "Nuts and Seeds"
        }
    ]
},
{
    "title": "The Green Drink",
    "ingredients": [
        {
            "ingredient": "honeydew melon",
            "serving_quantity": "90 grams",
            "category": "Other Fruits"
        },
        {
            "ingredient": "kiwis",
            "serving_quantity": "1",
            "category": "Other Fruits"
        }
    ]
}
"""
