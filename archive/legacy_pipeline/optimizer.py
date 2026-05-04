import math
import time
from datetime import datetime, date
import pandas as pd
from sqlalchemy import text
from pyomo.environ import ConcreteModel, Var, Set, Binary, NonNegativeReals, Constraint, Objective, maximize
from pyomo.opt import SolverFactory, TerminationCondition
from .db import get_engine
from .config import load_config


def _recency_penalty(last_date, half_life_days):
    if not last_date:
        return 0.0
    if isinstance(last_date, str):
        last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
    if isinstance(last_date, datetime):
        last_date = last_date.date()
    delta = (date.today() - last_date).days
    return math.exp(-delta / float(half_life_days))


def _load_data(engine):
    recipes = pd.read_sql(
        "SELECT recipe_id, title, rating FROM meal_planning.recipe",
        engine,
    )
    meal_types = pd.read_sql(
        "SELECT recipe_id, meal_type FROM meal_planning.recipe_meal_type",
        engine,
    )
    ingredients = pd.read_sql(
        """
        SELECT recipe_id, ingredient_canonical, food_group, portion_met, portions
        FROM meal_planning.recipe_ingredient
        WHERE ingredient_canonical IS NOT NULL AND food_group IS NOT NULL
        """,
        engine,
    )
    nutrition = pd.read_sql(
        "SELECT recipe_id, per_serving_kcal, per_serving_fiber_g FROM meal_planning.recipe_nutrition",
        engine,
    )
    history = pd.read_sql(
        "SELECT recipe_id, max(planned_for) AS last_planned FROM meal_planning.meal_history GROUP BY recipe_id",
        engine,
    )
    return recipes, meal_types, ingredients, nutrition, history


def optimize_plan(config_path=None):
    cfg = load_config(config_path)
    engine = get_engine()

    recipes, meal_types_df, ingredients_df, nutrition_df, history_df = _load_data(engine)
    if recipes.empty:
        raise RuntimeError("No recipes found")

    meal_types = cfg.get("meal_types", ["breakfast", "lunch", "dinner", "snack"])
    targets = cfg.get("daily_dozen_targets", {})
    portion_sizes = cfg.get("portion_sizes", {})
    opt_cfg = cfg.get("optimizer", {})

    min_rating = opt_cfg.get("min_rating", 3)
    rating_weight = opt_cfg.get("rating_weight", 1.0)
    diversity_weight = opt_cfg.get("diversity_weight", 1.0)
    recency_weight = opt_cfg.get("recency_weight", 0.8)
    slack_weight = opt_cfg.get("slack_weight", 5.0)
    half_life = opt_cfg.get("recency_half_life_days", 30)
    calories_min = opt_cfg.get("calories_daily_min")
    calories_max = opt_cfg.get("calories_daily_max")
    fiber_min = opt_cfg.get("fiber_daily_min")
    calories_weekly_min = opt_cfg.get("calories_weekly_min")
    calories_weekly_max = opt_cfg.get("calories_weekly_max")
    fiber_weekly_min = opt_cfg.get("fiber_weekly_min")
    weekly_group_portions_min = opt_cfg.get("weekly_group_portions_min") or {}
    snack_optional = bool(opt_cfg.get("snack_optional", False))
    max_repeats = opt_cfg.get("max_recipe_repeats", 2)
    solver_time_limit = opt_cfg.get("solver_time_limit")
    horizon_days = opt_cfg.get("planning_horizon_days", 7)

    recipes = recipes[recipes["rating"].fillna(0) >= min_rating]
    if recipes.empty:
        raise RuntimeError("No recipes meet minimum rating")

    missing_groups = [g for g in targets.keys() if g not in portion_sizes]
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "portion_size_missing_groups", "value": len(missing_groups)},
        )

    R = recipes["recipe_id"].tolist()
    D = list(range(1, int(horizon_days) + 1))
    M = meal_types

    nutrition_df = nutrition_df.set_index("recipe_id")
    rating = {r: float(recipes.loc[recipes["recipe_id"] == r, "rating"].iloc[0] or 0) for r in R}
    kcal = {r: float(nutrition_df.loc[r, "per_serving_kcal"]) if r in nutrition_df.index else 0.0 for r in R}
    fiber = {r: float(nutrition_df.loc[r, "per_serving_fiber_g"]) if r in nutrition_df.index else 0.0 for r in R}

    history_map = {row["recipe_id"]: row["last_planned"] for _, row in history_df.iterrows()}
    recency = {r: _recency_penalty(history_map.get(r), half_life) for r in R}

    if meal_types_df.empty:
        allowed_meal = {(r, m): 1 for r in R for m in M}
    else:
        mt = meal_types_df.groupby("recipe_id")["meal_type"].apply(list).to_dict()
        allowed_meal = {(r, m): 1 if m in mt.get(r, M) else 0 for r in R for m in M}

    ingredients_df = ingredients_df[ingredients_df["recipe_id"].isin(R)]
    daily_ingredients_df = ingredients_df[ingredients_df["portion_met"] == True]

    I = sorted(daily_ingredients_df["ingredient_canonical"].unique().tolist())
    G = list(targets.keys())

    portion_met = {(r, i): 0 for r in R for i in I}
    food_group = {}
    portions = {(r, i): 0.0 for r in R for i in I}

    for _, row in daily_ingredients_df.iterrows():
        r = row["recipe_id"]
        i = row["ingredient_canonical"]
        portion_met[(r, i)] = 1
        food_group[i] = row["food_group"]
        portions[(r, i)] = float(row["portions"] or 0.0)

    group_portions = {(r, g): 0.0 for r in R for g in G}
    for _, row in ingredients_df.iterrows():
        r = row["recipe_id"]
        g = row["food_group"]
        if r in R and g in G:
            group_portions[(r, g)] += float(row["portions"] or 0.0)

    model = ConcreteModel()
    model.D = Set(initialize=D)
    model.M = Set(initialize=M)
    model.R = Set(initialize=R)
    model.I = Set(initialize=I)
    model.G = Set(initialize=G)

    model.x = Var(model.R, model.D, model.M, domain=Binary)
    model.z = Var(model.D, model.I, domain=Binary)
    model.y = Var(model.I, domain=Binary)

    model.slack_group = Var(model.D, model.G, domain=NonNegativeReals)
    model.slack_weekly_group = Var(model.G, domain=NonNegativeReals)
    if calories_min is not None:
        model.slack_cal_min = Var(model.D, domain=NonNegativeReals)
    if calories_max is not None:
        model.slack_cal_max = Var(model.D, domain=NonNegativeReals)
    if fiber_min is not None:
        model.slack_fiber = Var(model.D, domain=NonNegativeReals)
    if calories_weekly_min is not None:
        model.slack_weekly_cal_min = Var(domain=NonNegativeReals)
    if calories_weekly_max is not None:
        model.slack_weekly_cal_max = Var(domain=NonNegativeReals)
    if fiber_weekly_min is not None:
        model.slack_weekly_fiber = Var(domain=NonNegativeReals)
    if snack_optional and "snack" in M:
        model.slack_snack = Var(model.D, domain=NonNegativeReals)

    def meal_slot_rule(m, d, meal):
        expr = sum(m.x[r, d, meal] for r in m.R)
        if snack_optional and meal == "snack":
            return expr + m.slack_snack[d] == 1
        return expr == 1

    model.meal_slot = Constraint(model.D, model.M, rule=meal_slot_rule)

    def allowed_rule(m, r, d, meal):
        return m.x[r, d, meal] <= allowed_meal[(r, meal)]

    model.allowed = Constraint(model.R, model.D, model.M, rule=allowed_rule)

    def repeat_rule(m, r):
        return sum(m.x[r, d, meal] for d in m.D for meal in m.M) <= max_repeats

    model.repeat_limit = Constraint(model.R, rule=repeat_rule)

    def ingredient_use_rule(m, d, i):
        return m.z[d, i] <= sum(portion_met[(r, i)] * m.x[r, d, meal] for r in m.R for meal in m.M)

    model.ingredient_use = Constraint(model.D, model.I, rule=ingredient_use_rule)

    def ingredient_global_lower(m, i):
        return m.y[i] <= sum(m.z[d, i] for d in m.D)

    def ingredient_global_upper(m, i):
        return sum(m.z[d, i] for d in m.D) <= len(D) * m.y[i]

    model.ingredient_global_lower = Constraint(model.I, rule=ingredient_global_lower)
    model.ingredient_global_upper = Constraint(model.I, rule=ingredient_global_upper)

    def group_rule(m, d, g):
        return sum(m.z[d, i] for i in m.I if food_group.get(i) == g) + m.slack_group[d, g] >= targets[g]

    model.group_min = Constraint(model.D, model.G, rule=group_rule)

    def weekly_group_rule(m, g):
        target = weekly_group_portions_min.get(g)
        if target is None:
            target = int(targets.get(g, 0)) * len(D)
        return sum(group_portions[(r, g)] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M) + m.slack_weekly_group[g] >= target

    model.weekly_group_min = Constraint(model.G, rule=weekly_group_rule)

    if calories_min is not None:
        def cal_min_rule(m, d):
            return sum(kcal[r] * m.x[r, d, meal] for r in m.R for meal in m.M) + m.slack_cal_min[d] >= calories_min
        model.cal_min = Constraint(model.D, rule=cal_min_rule)

    if calories_max is not None:
        def cal_max_rule(m, d):
            return sum(kcal[r] * m.x[r, d, meal] for r in m.R for meal in m.M) - m.slack_cal_max[d] <= calories_max
        model.cal_max = Constraint(model.D, rule=cal_max_rule)

    if fiber_min is not None:
        def fiber_min_rule(m, d):
            return sum(fiber[r] * m.x[r, d, meal] for r in m.R for meal in m.M) + m.slack_fiber[d] >= fiber_min
        model.fiber_min = Constraint(model.D, rule=fiber_min_rule)

    if calories_weekly_min is not None:
        def weekly_cal_min(m):
            return sum(kcal[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M) + m.slack_weekly_cal_min >= calories_weekly_min
        model.weekly_cal_min = Constraint(rule=weekly_cal_min)

    if calories_weekly_max is not None:
        def weekly_cal_max(m):
            return sum(kcal[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M) - m.slack_weekly_cal_max <= calories_weekly_max
        model.weekly_cal_max = Constraint(rule=weekly_cal_max)

    if fiber_weekly_min is not None:
        def weekly_fiber_min(m):
            return sum(fiber[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M) + m.slack_weekly_fiber >= fiber_weekly_min
        model.weekly_fiber_min = Constraint(rule=weekly_fiber_min)

    def objective_rule(m):
        diversity = sum(m.y[i] for i in m.I)
        rating_term = sum(rating[r] * sum(m.x[r, d, meal] for d in m.D for meal in m.M) for r in m.R)
        recency_term = sum(recency[r] * sum(m.x[r, d, meal] for d in m.D for meal in m.M) for r in m.R)
        slack = sum(m.slack_group[d, g] for d in m.D for g in m.G)
        slack += sum(m.slack_weekly_group[g] for g in m.G)
        if calories_min is not None:
            slack += sum(m.slack_cal_min[d] for d in m.D)
        if calories_max is not None:
            slack += sum(m.slack_cal_max[d] for d in m.D)
        if fiber_min is not None:
            slack += sum(m.slack_fiber[d] for d in m.D)
        if calories_weekly_min is not None:
            slack += m.slack_weekly_cal_min
        if calories_weekly_max is not None:
            slack += m.slack_weekly_cal_max
        if fiber_weekly_min is not None:
            slack += m.slack_weekly_fiber
        if snack_optional and "snack" in M:
            slack += sum(m.slack_snack[d] for d in m.D)
        return diversity_weight * diversity + rating_weight * rating_term - recency_weight * recency_term - slack_weight * slack

    model.objective = Objective(rule=objective_rule, sense=maximize)

    solver = SolverFactory("glpk")
    if solver_time_limit:
        try:
            solver.options["tmlim"] = int(solver_time_limit)
        except Exception:
            pass
    start = time.time()
    res = solver.solve(model, tee=False)
    solver_seconds = time.time() - start
    if res.solver.termination_condition != TerminationCondition.optimal:
        raise RuntimeError(f"Solver status: {res.solver.termination_condition}")

    plan = {d: {m: None for m in M} for d in D}
    for d in D:
        for m in M:
            for r in R:
                if model.x[r, d, m].value and model.x[r, d, m].value > 0.5:
                    plan[d][m] = r

    slack_total = sum(model.slack_group[d, g].value for d in D for g in G)
    slack_total += sum(model.slack_weekly_group[g].value for g in G)
    if calories_min is not None:
        slack_total += sum(model.slack_cal_min[d].value for d in D)
    if calories_max is not None:
        slack_total += sum(model.slack_cal_max[d].value for d in D)
    if fiber_min is not None:
        slack_total += sum(model.slack_fiber[d].value for d in D)
    if calories_weekly_min is not None:
        slack_total += model.slack_weekly_cal_min.value
    if calories_weekly_max is not None:
        slack_total += model.slack_weekly_cal_max.value
    if fiber_weekly_min is not None:
        slack_total += model.slack_weekly_fiber.value
    if snack_optional and "snack" in M:
        slack_total += sum(model.slack_snack[d].value for d in D)

    return {
        "plan": plan,
        "solver_status": str(res.solver.termination_condition),
        "solver_seconds": solver_seconds,
        "slack_total": float(slack_total),
    }


def write_plan(result, config_path=None):
    cfg = load_config(config_path)
    targets = cfg.get("daily_dozen_targets", {})
    plan = result["plan"]

    engine = get_engine()
    run_time = datetime.utcnow()

    with engine.begin() as conn:
        plan_run_id = conn.execute(
            text(
                """
                INSERT INTO meal_planning.plan_run (run_time, status, solver_status, solver_seconds, slack_total)
                VALUES (:run_time, :status, :solver_status, :solver_seconds, :slack_total)
                RETURNING plan_run_id
                """
            ),
            {
                "run_time": run_time,
                "status": "ok",
                "solver_status": result.get("solver_status"),
                "solver_seconds": result.get("solver_seconds"),
                "slack_total": result.get("slack_total"),
            },
        ).scalar()

        recipe_ids = [r for day in plan.values() for r in day.values() if r]
        if recipe_ids:
            recipe_ids = list(set(recipe_ids))

        nutrition = pd.read_sql(
            "SELECT recipe_id, per_serving_kcal, per_serving_fiber_g FROM meal_planning.recipe_nutrition",
            conn,
        ).set_index("recipe_id")

        ingredients = pd.read_sql(
            """
            SELECT recipe_id, ingredient_canonical, food_group, portions, portion_met
            FROM meal_planning.recipe_ingredient
            WHERE ingredient_canonical IS NOT NULL AND food_group IS NOT NULL
            """,
            conn,
        )

        daily_violations = 0

        total_kcal = 0.0
        total_fiber = 0.0

        for d, meals in plan.items():
            day_kcal = 0.0
            day_fiber = 0.0
            selected = [r for r in meals.values() if r]

            for m, r in meals.items():
                conn.execute(
                    text("INSERT INTO meal_planning.plan_meal (plan_run_id, day, meal_type, recipe_id) VALUES (:plan_run_id, :day, :meal_type, :recipe_id)"),
                    {"plan_run_id": plan_run_id, "day": d, "meal_type": m, "recipe_id": r},
                )

            for r in selected:
                if r in nutrition.index:
                    day_kcal += float(nutrition.loc[r, "per_serving_kcal"] or 0.0)
                    day_fiber += float(nutrition.loc[r, "per_serving_fiber_g"] or 0.0)

            conn.execute(
                text("INSERT INTO meal_planning.plan_day (plan_run_id, day, kcal, fiber_g) VALUES (:plan_run_id, :day, :kcal, :fiber_g)"),
                {"plan_run_id": plan_run_id, "day": d, "kcal": day_kcal, "fiber_g": day_fiber},
            )

            total_kcal += day_kcal
            total_fiber += day_fiber

            day_ingredients = ingredients[ingredients["recipe_id"].isin(selected)]
            day_ingredients = day_ingredients[day_ingredients["portion_met"] == True]
            grouped = day_ingredients.groupby("food_group")

            for g in targets.keys():
                if g in grouped.groups:
                    subset = grouped.get_group(g)
                    daily_count = subset["ingredient_canonical"].nunique()
                    daily_portions = float(subset["portions"].fillna(0).sum())
                else:
                    daily_count = 0
                    daily_portions = 0.0
                if daily_count < int(targets[g]):
                    daily_violations += 1
                conn.execute(
                    text(
                        """
                        INSERT INTO meal_planning.plan_day_group (plan_run_id, day, food_group, daily_count, daily_portions)
                        VALUES (:plan_run_id, :day, :food_group, :daily_count, :daily_portions)
                        """
                    ),
                    {
                        "plan_run_id": plan_run_id,
                        "day": d,
                        "food_group": g,
                        "daily_count": int(daily_count),
                        "daily_portions": daily_portions,
                    },
                )

            for r in selected:
                for meal_type in meals:
                    if meals[meal_type] == r:
                        conn.execute(
                            text(
                                "INSERT INTO meal_planning.meal_history (recipe_id, meal_type, planned_for) VALUES (:recipe_id, :meal_type, :planned_for) ON CONFLICT DO NOTHING"
                            ),
                            {"recipe_id": r, "meal_type": meal_type, "planned_for": date.today()},
                        )

        all_selected = ingredients[ingredients["recipe_id"].isin(recipe_ids)]
        unique_ingredients = all_selected["ingredient_canonical"].nunique()
        unique_food_groups = all_selected["food_group"].nunique()
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "plan_unique_ingredients", "value": unique_ingredients},
        )
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "plan_unique_food_groups", "value": unique_food_groups},
        )
        conn.execute(
            text("INSERT INTO meal_planning.pipeline_metric (metric_name, metric_value) VALUES (:name, :value)"),
            {"name": "plan_daily_dozen_violations", "value": daily_violations},
        )

        conn.execute(
            text("UPDATE meal_planning.plan_run SET total_kcal = :kcal, total_fiber = :fiber WHERE plan_run_id = :plan_run_id"),
            {"kcal": float(total_kcal), "fiber": float(total_fiber), "plan_run_id": plan_run_id},
        )

    return plan_run_id
