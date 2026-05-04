import os
import pandas as pd
# pd.set_option('display.max_columns', None)
from pyomo.environ import *
from db_manager import DatabaseManager
import logging
from datetime import datetime
from sqlalchemy import text
from pyomo.opt import SolverFactory, TerminationCondition

# Configure logging.
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Reduce Pyomo's internal logging verbosity.
logging.getLogger('pyomo').setLevel(logging.WARNING)

# Global model constants.
DAYS = list(range(1, 8))  # Days 1 through 7
MEAL_TYPES = ['breakfasts', 'lunches', 'dinner', 'snacks']
DEFAULT_MAX_OCCURRENCE = 5

# Define the daily requirements for each category.
CATEGORY_REQUIREMENTS = {
    'Beans': 3,
    'Berries': 1,
    'Other Fruits': 2,
    'Cruciferous Vegetables': 1,
    'Greens': 2,
    'Other Vegetables': 2,
    # 'Flaxseeds or Linseeds': 1,
    'Nuts and Seeds': 1,
    'Herbs and Spices': 1,
    'Whole Grains': 2
}
MAX_RELAXATIONS = 15


def build_model_parameters(recipe_data):
    logger.info("Building model parameters from recipe data with %d rows", len(recipe_data))
    R = recipe_data['title'].unique().tolist()
    I = recipe_data['ingredient'].unique().tolist()
    allowed_meal = {(r, m): int(recipe_data.loc[recipe_data['title']==r, m].iloc[0])
                    for r in R for m in MEAL_TYPES}
    max_occurrence = {r: DEFAULT_MAX_OCCURRENCE for r in R}
    A = {(r, i): 0 for r in R for i in I}
    for _, row in recipe_data.iterrows():
        A[(row['title'], row['ingredient'])] = 1
    cat = {i: None for i in I}
    for _, row in recipe_data.iterrows():
        c = row['category']
        cat[row['ingredient']] = 'Flaxseeds or Linseeds' if c in ['Flaxseeds','Linseeds'] else c
    req = CATEGORY_REQUIREMENTS.copy()
    logger.info("Model parameters: %d recipes, %d ingredients", len(R), len(I))
    return R, I, allowed_meal, max_occurrence, A, cat, req


def build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req):
    logger.info("Building Pyomo model")
    model = ConcreteModel()
    model.D = Set(initialize=DAYS)
    model.M = Set(initialize=MEAL_TYPES)
    model.R = Set(initialize=R)
    model.I = Set(initialize=I)
    model.C = Set(initialize=list(req.keys()))
    model.x = Var(model.R, model.D, model.M, domain=Binary)
    model.z = Var(model.D, model.I, domain=Binary)
    model.y = Var(model.I, domain=Binary)
    model.allowed_meal_constraint = Constraint(model.R, model.D, model.M,
        rule=lambda m,r,d,meal: m.x[r,d,meal] <= allowed_meal[(r,meal)])
    model.meal_slot_constraint = Constraint(model.D, model.M,
        rule=lambda m,d,meal: sum(m.x[r,d,meal] for r in m.R)==1)
    model.recipe_occurrence = Constraint(model.R,
        rule=lambda m,r: sum(m.x[r,d,meal] for d in m.D for meal in m.M)<=max_occurrence[r])
    model.ingredient_usage = Constraint(model.D, model.I,
        rule=lambda m,d,i: m.z[d,i] <= sum(A[(r,i)]*m.x[r,d,meal] for r in m.R for meal in m.M))
    model.ingredient_global_lower = Constraint(model.I,
        rule=lambda m,i: m.y[i] <= sum(m.z[d,i] for d in m.D))
    model.ingredient_global_upper = Constraint(model.I,
        rule=lambda m,i: sum(m.z[d,i] for d in m.D) <= len(DAYS)*m.y[i])
    model.category_constraint = Constraint(model.D, model.C,
        rule=lambda m,d,c: sum(m.z[d,i] for i in m.I if cat[i]==c) >= req[c])
    model.objective = Objective(rule=lambda m: sum(m.y[i] for i in m.I), sense=maximize)
    logger.info("Pyomo model built with %d decision vars", len(model.x))
    return model


def main():
    logger.info("Starting weekly summary generation")
    db_manager = DatabaseManager()

    logger.info("Loading processed_recipes from database")
    processed_df = pd.read_sql(
        f"SELECT title, ingredient, serving_quantity, category, "
        + ", ".join(MEAL_TYPES) + ", lastmodifieddate FROM meal_planning.processed_recipes",
        db_manager.engine)
    logger.info("Loaded %d rows from processed_recipes", len(processed_df))

    # build & solve
    R, I, allowed_meal, max_occurrence, A, cat, req = build_model_parameters(processed_df)
    model = build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req)
    solver = SolverFactory('glpk')

    logger.info("Solving model")
    relax = 0
    while True:
        res = solver.solve(model, tee=False)
        tc = res.solver.termination_condition
        if tc == TerminationCondition.optimal:
            logger.info("Optimal solution found")
            break
        if tc in [TerminationCondition.infeasible, TerminationCondition.infeasibleOrUnbounded] and relax<MAX_RELAXATIONS:
            logger.warning("Infeasible, relaxing constraints (step %d)", relax+1)
            for c in req:
                if req[c]>0:
                    req[c]-=1
                    break
            model = build_pyomo_model(R, I, allowed_meal, max_occurrence, A, cat, req)
            relax+=1
            continue
        logger.error("Solver terminated with condition: %s", tc)
        return

    # extract plan
    logger.info("Extracting meal plan")
    plan = {d:{m: next((r for r in R if model.x[r,d,m].value>0.5), None) for m in MEAL_TYPES} for d in DAYS}

    # build summary rows
    logger.info("Building summary rows")
    rows=[]
    for d in DAYS:
        row={'run_time': datetime.utcnow(), 'week_number': None, 'day': d}
        for m in MEAL_TYPES:
            row[m]=plan[d][m]
        day_df = processed_df[processed_df['title'].isin(list(plan[d].values()))]
        counts = day_df.groupby('category')['ingredient'].nunique()
        for c in CATEGORY_REQUIREMENTS:
            row[f"{c.replace(' ','_').lower()}_count"] = int(counts.get(c,0))
        rows.append(row)
    summary = pd.DataFrame(rows)
    logger.info("Constructed summary DataFrame with %d rows", len(summary))

    # determine next week_number
    logger.info("Determining next week number")
    with db_manager.engine.connect() as conn:
        result = conn.execute(text(f"SELECT max(week_number) FROM meal_planning.weekly_meal_plan"))
        prev = result.scalar() or 0
    summary['week_number'] = prev + 1
    logger.info("Assigned week_number %d to all rows", prev+1)

    # reorder columns
    cols = ['run_time','week_number','day'] + MEAL_TYPES + [f"{c.replace(' ','_').lower()}_count" for c in CATEGORY_REQUIREMENTS]
    summary = summary[cols]

    # append summary
    logger.info("Inserting summary into database")
    summary.to_sql('weekly_meal_plan', db_manager.engine, schema='meal_planning', if_exists='append', index=False)

    logger.info("Weekly summary generation complete. Here is the result:")
    print(summary.to_string(index=False))

if __name__ == '__main__':
    main()
