import os
import pandas as pd
# pd.set_option('display.max_columns', None)
from pyomo.environ import *
from db_manager import DatabaseManager
import logging
from datetime import datetime
from pyomo.opt import SolverFactory, TerminationCondition

# Configure logging.
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Reduce Pyomo's internal logging verbosity.
logging.getLogger('pyomo').setLevel(logging.WARNING)

# Global model constants.
DAYS = list(range(1, 8))  # Days 1 through 7
MEAL_TYPES = ['Breakfasts', 'Lunches', 'Dinner', 'Snacks'] #, 'Side Salad']
DEFAULT_MAX_OCCURRENCE = 5
# Define the daily requirements for each category.
# Note: We have replaced "Flaxseeds" with "Flaxseeds or Linseeds".
CATEGORY_REQUIREMENTS = {
    'Beans': 3,
    'Berries': 1,
    'Other Fruits': 3,
    'Cruciferous Vegetables': 1,
    'Greens': 2,
    'Other Vegetables': 2,
    'Flaxseeds or Linseeds': 1,
    'Nuts and Seeds': 1,
    'Herbs and Spices': 1,
    'Whole Grains': 3
}
MAX_RELAXATIONS = 15

def build_model_parameters(recipe_data):
    """
    Build model parameters for the Pyomo meal-planning model using a merged DataFrame.
    
    Parameters:
      recipe_data (DataFrame): Merged DataFrame from meals_df and processed_df,
                               containing at least the columns 'title', 'meal_type',
                               'ingredient', and 'category'.
    
    Returns:
      R: list of unique recipe titles.
      I: list of unique ingredients.
      allowed_meal: dict mapping (recipe, meal) -> 0/1 indicating if the recipe is allowed for that meal.
      max_occurrence: dict mapping recipe -> maximum number of occurrences (default constant).
      A: dict mapping (recipe, ingredient) -> 0/1 (1 if the recipe uses the ingredient).
      cat: dict mapping each ingredient -> its category (merging "Flaxseeds" and "Linseeds").
      req: dictionary of daily category requirements (a copy of CATEGORY_REQUIREMENTS).
    """
    # Assume recipe_data has columns: 'title', 'meal_type', 'ingredient', 'category'
    R = recipe_data['title'].unique().tolist()
    I = recipe_data['ingredient'].unique().tolist()
    
    # Build allowed_meal: for each recipe, determine which meal types it is allowed for.
    allowed_meal = {}
    for r in R:
        # Get the first row for this recipe.
        row = recipe_data[recipe_data['title'] == r].iloc[0]
        mt_value = row['meal_type']
        if isinstance(mt_value, list):
            allowed = mt_value
        else:
            allowed = [m.strip() for m in mt_value.split(',')]
        for m in MEAL_TYPES:
            allowed_meal[(r, m)] = 1 if m in allowed else 0
    
    # Set maximum occurrences for each recipe.
    max_occurrence = {r: DEFAULT_MAX_OCCURRENCE for r in R}
    
    # Build the mapping A: (recipe, ingredient) -> 0/1.
    A = {}
    for r in R:
        for ing in I:
            # If any row in recipe_data has this recipe and ingredient, set A[(r, ing)] = 1.
            if not recipe_data[(recipe_data['title'] == r) & (recipe_data['ingredient'] == ing)].empty:
                A[(r, ing)] = 1
            else:
                A[(r, ing)] = 0
    
    # Build the category mapping for each ingredient.
    cat = {}
    for ing in I:
        rows = recipe_data[recipe_data['ingredient'] == ing]
        if not rows.empty:
            value = rows.iloc[0]['category']
            # Merge "Flaxseeds" and "Linseeds" into one category.
            if value in ["Flaxseeds", "Linseeds"]:
                cat[ing] = "Flaxseeds or Linseeds"
            else:
                cat[ing] = value
        else:
            cat[ing] = None
    
    # Use a copy of the global CATEGORY_REQUIREMENTS as the requirements.
    req = CATEGORY_REQUIREMENTS.copy()
    
    return R, I, allowed_meal, max_occurrence, A, cat, req


def build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req):
    model = ConcreteModel()
    
    model.D = Set(initialize=DAYS)
    model.M = Set(initialize=MEAL_TYPES)
    model.R = Set(initialize=R)
    model.I = Set(initialize=I)
    model.C = Set(initialize=list(req.keys()))
    
    # Decision variable: x[r,d,m] = 1 if recipe r is selected on day d for meal m.
    model.x = Var(model.R, model.D, model.M, domain=Binary)
    
    # Linking variable: z[d,i] = 1 if ingredient i is used on day d.
    model.z = Var(model.D, model.I, domain=Binary)
    
    # Global usage: y[i] = 1 if ingredient i is used in the week.
    model.y = Var(model.I, domain=Binary)
    
    # --- New Constraint: Ensure that a recipe can only be selected for a meal slot if it is allowed.
    def allowed_meal_rule(model, r, d, m):
        return model.x[r, d, m] <= allowed_meal[(r, m)]
    model.allowed_meal_constraint = Constraint(model.R, model.D, model.M, rule=allowed_meal_rule)
    
    # Constraint: Exactly one recipe per day per meal slot.
    def meal_slot_rule(model, d, m):
        return sum(model.x[r, d, m] for r in model.R) == 1
    model.meal_slot_constraint = Constraint(model.D, model.M, rule=meal_slot_rule)
    
    # Constraint: Each recipe appears at most max_occurrence times.
    def recipe_occurrence_rule(model, r):
        return sum(model.x[r, d, m] for d in model.D for m in model.M) <= max_occurrence[r]
    model.recipe_occurrence = Constraint(model.R, rule=recipe_occurrence_rule)
    
    # Linking constraint: Ingredient usage on day d.
    def ingredient_usage_rule(model, d, i):
        return model.z[d, i] <= sum(A[(r, i)] * model.x[r, d, m] for r in model.R for m in model.M)
    model.ingredient_usage = Constraint(model.D, model.I, rule=ingredient_usage_rule)
    
    # Global linking: y[i] is 1 if ingredient i is used on any day.
    def ingredient_global_lower(model, i):
        return model.y[i] <= sum(model.z[d, i] for d in model.D)
    model.ingredient_global_lower = Constraint(model.I, rule=ingredient_global_lower)
    
    def ingredient_global_upper(model, i):
        return sum(model.z[d, i] for d in model.D) <= len(DAYS) * model.y[i]
    model.ingredient_global_upper = Constraint(model.I, rule=ingredient_global_upper)
    
    # Category constraint: For each day and each category c, at least req[c] distinct ingredients must be used.
    def category_day_rule(model, d, c):
        return sum(model.z[d, i] for i in model.I if cat[i] == c) >= req[c]
    model.category_constraint = Constraint(model.D, model.C, rule=category_day_rule)
    
    # Objective: maximize the number of distinct ingredients used over the week.
    def objective_rule(model):
        return sum(model.y[i] for i in model.I)
    model.objective = Objective(rule=objective_rule, sense=maximize)
    
    return model

# def compute_plant_diversity_by_recipe(meals_df):
#     # Compute diversity: for each recipe, count unique ingredients overall and per category.
#     # First, group by recipe title and category.
#     diversity_by_category = meals_df.groupby(['title', 'meal_type'])['ingredient'].nunique().unstack(fill_value=0)
#     diversity_by_category['total'] = diversity_by_category.sum(axis=1)
    
#     # Merge the meal_type information.
#     meal_diversity = pd.merge(meals_df[['title', 'meal_type']], diversity_by_category, on='title')
    
#     # For each meal type, log the top recipes (by unique ingredient count).
#     for meal in MEAL_TYPES:
#         subset = meal_diversity[meal_diversity['meal_type'].apply(lambda lst: meal in lst)]
#         top_recipes = subset.sort_values(by='total', ascending=False).head(5)
#         logger.info("Top recipes for %s (by unique ingredient count):", meal)
#         for _, row in top_recipes.iterrows():
#             # You can also log the breakdown by category if desired.
#             logger.info("Recipe: %s, Total Unique Ingredients: %d, Breakdown: %s",
#                         row['title'], row['total'], row.drop(['title', 'meal_type', 'total']).to_dict())
    

def main():
    # Instantiate DatabaseManager and load data.
    db_manager = DatabaseManager()
    
    processed_df = pd.read_sql(
        "SELECT title, ingredient, serving_quantity, category FROM meal_planning.processed_recipes",
        db_manager.engine
    )
    logger.info("Fetched processed_recipes: %d rows", len(processed_df))
    
    meals_df = pd.read_sql(
        "SELECT title, categories, rating, difficulty FROM meal_planning.recipes",
        db_manager.engine
    )
    logger.info("Fetched recipes: %d rows", len(meals_df))
    
    # Create meal_type column by parsing the categories field.
    meals_df['meal_type'] = meals_df['categories'].apply(
        lambda x: x if isinstance(x, list) else [m.strip() for m in x.split(',') if m.strip() in MEAL_TYPES]
    )

    meals_df.drop(columns=['categories'], inplace=True)

    recipe_data = pd.merge(meals_df, processed_df, on='title')


    # print(meals_df[['title', 'meal_type']].head())
    # print(recipe_data[['title', 'ingredient', 'category', 'meal_type']].head())

    # print the top recipes for each meal type
    # compute_plant_diversity_by_recipe(meals_df)
    
    # Build model parameters.
    R, I, allowed_meal, max_occurrence, A, cat, req = build_model_parameters(recipe_data)
    
    # Build the Pyomo model.
    model = build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req)
    
    # Create a solver.
    solver = SolverFactory('glpk')
    
    # Attempt to solve with potential relaxation if infeasible.
    relaxations = 0
    while True:
        results = solver.solve(model, tee=False)
        term_cond = results.solver.termination_condition
        if term_cond == TerminationCondition.optimal:
            logger.info("Solver found an optimal solution.")
            break
        elif term_cond in [TerminationCondition.infeasible, TerminationCondition.infeasibleOrUnbounded]:
            if relaxations < MAX_RELAXATIONS:
                # Relax one category constraint: reduce the requirement by 1 for the first category that is > 0.
                for c in req:
                    if req[c] > 0:
                        req[c] -= 1
                        logger.info("Relaxing requirement for category '%s' to %d", c, req[c])
                        break
                # Rebuild the model with the new requirements.
                model = build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req)
                relaxations += 1
            else:
                logger.error("Unable to find a feasible solution after %d relaxations.", MAX_RELAXATIONS)
                return
        else:
            logger.error("Solver terminated with condition: %s", term_cond)
            return

    # Extract the meal plan from the solution.
    meal_plan = {}
    for d in model.D:
        meal_plan[d] = {}
        for m in model.M:
            for r in model.R:
                if value(model.x[r, d, m]) > 0.5:
                    meal_plan[d][m] = r
    logger.info("Meal plan for the week: %s", meal_plan)
    
if __name__ == '__main__':
    main()
