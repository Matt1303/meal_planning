from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    NonNegativeReals,
    Objective,
    Set,
    Var,
    maximize,
)

from meal_planner.config import Settings
from meal_planner.optimize.data import PreparedData


@dataclass(frozen=True)
class ModelOptions:
    enforce_daily_kcal: bool
    enforce_daily_fiber: bool
    enforce_daily_protein: bool
    enforce_weekly_kcal: bool
    enforce_weekly_fiber: bool
    enforce_weekly_protein: bool
    enforce_group_targets: bool
    enforce_weekly_groups: bool


def build_model(prepared: PreparedData, settings: Settings, options: ModelOptions) -> Any:
    opt = settings.optimizer
    targets = settings.daily_dozen_targets

    model = ConcreteModel()
    model.D = Set(initialize=prepared.days)
    model.M = Set(initialize=prepared.meal_types)
    model.R = Set(initialize=prepared.recipes)
    model.I = Set(initialize=prepared.ingredients_canonical)
    model.G = Set(initialize=prepared.food_groups)

    model.x = Var(model.R, model.D, model.M, domain=Binary)
    model.z = Var(model.D, model.I, domain=Binary)
    model.y = Var(model.I, domain=Binary)

    model.slack_group = Var(model.D, model.G, domain=NonNegativeReals)
    model.slack_weekly_group = Var(model.G, domain=NonNegativeReals)

    if options.enforce_daily_kcal and opt.calories_daily_min is not None:
        model.slack_cal_min = Var(model.D, domain=NonNegativeReals)
    if options.enforce_daily_kcal and opt.calories_daily_max is not None:
        model.slack_cal_max = Var(model.D, domain=NonNegativeReals)
    if options.enforce_daily_fiber and opt.fiber_daily_min is not None:
        model.slack_fiber_min = Var(model.D, domain=NonNegativeReals)
    if options.enforce_daily_fiber and opt.fiber_daily_max is not None:
        model.slack_fiber_max = Var(model.D, domain=NonNegativeReals)
    if options.enforce_daily_protein and opt.protein_daily_min is not None:
        model.slack_protein_min = Var(model.D, domain=NonNegativeReals)
    if options.enforce_daily_protein and opt.protein_daily_max is not None:
        model.slack_protein_max = Var(model.D, domain=NonNegativeReals)
    if options.enforce_weekly_kcal and opt.calories_weekly_min is not None:
        model.slack_weekly_cal_min = Var(domain=NonNegativeReals)
    if options.enforce_weekly_kcal and opt.calories_weekly_max is not None:
        model.slack_weekly_cal_max = Var(domain=NonNegativeReals)
    if options.enforce_weekly_fiber and opt.fiber_weekly_min is not None:
        model.slack_weekly_fiber = Var(domain=NonNegativeReals)
    if options.enforce_weekly_protein and opt.protein_weekly_min is not None:
        model.slack_weekly_protein = Var(domain=NonNegativeReals)
    if opt.snack_optional and "snack" in prepared.meal_types:
        model.slack_snack = Var(model.D, domain=NonNegativeReals)

    pairs = [(d1, d2) for d1 in prepared.days for d2 in prepared.days if d1 < d2]
    penalty_by_gap = opt.spacing_penalty_by_gap or {}
    relevant_pairs = [(d1, d2) for d1, d2 in pairs if penalty_by_gap.get(d2 - d1, 0.0) > 0]
    spacing_active = opt.spacing_weight > 0 and bool(relevant_pairs) and opt.max_recipe_repeats > 1
    if spacing_active:
        model.PAIRS = Set(initialize=relevant_pairs, dimen=2)
        model.recipe_pair = Var(model.R, model.PAIRS, domain=Binary)

    snack_optional = opt.snack_optional and "snack" in prepared.meal_types

    def meal_slot_rule(m: Any, d: int, meal: str) -> Any:
        expr = sum(m.x[r, d, meal] for r in m.R)
        if snack_optional and meal == "snack":
            return expr + m.slack_snack[d] == 1
        return expr == 1

    model.meal_slot = Constraint(model.D, model.M, rule=meal_slot_rule)

    allowed_meal = prepared.allowed_meal

    def allowed_rule(m: Any, r: int, d: int, meal: str) -> Any:
        return m.x[r, d, meal] <= allowed_meal[(r, meal)]

    model.allowed = Constraint(model.R, model.D, model.M, rule=allowed_rule)

    def repeat_rule(m: Any, r: int) -> Any:
        return sum(m.x[r, d, meal] for d in m.D for meal in m.M) <= opt.max_recipe_repeats

    model.repeat_limit = Constraint(model.R, rule=repeat_rule)

    def one_per_day_rule(m: Any, r: int, d: int) -> Any:
        return sum(m.x[r, d, meal] for meal in m.M) <= 1

    model.one_recipe_per_day = Constraint(model.R, model.D, rule=one_per_day_rule)

    portion_met = prepared.portion_met

    def ingredient_use_rule(m: Any, d: int, i: str) -> Any:
        return m.z[d, i] <= sum(portion_met[(r, i)] * m.x[r, d, meal] for r in m.R for meal in m.M)

    model.ingredient_use = Constraint(model.D, model.I, rule=ingredient_use_rule)

    def ingredient_lower(m: Any, i: str) -> Any:
        return m.y[i] <= sum(m.z[d, i] for d in m.D)

    def ingredient_upper(m: Any, i: str) -> Any:
        return sum(m.z[d, i] for d in m.D) <= len(prepared.days) * m.y[i]

    model.ingredient_global_lower = Constraint(model.I, rule=ingredient_lower)
    model.ingredient_global_upper = Constraint(model.I, rule=ingredient_upper)

    if options.enforce_group_targets:
        food_group_of = prepared.food_group_of

        def group_rule(m: Any, d: int, g: str) -> Any:
            return (
                sum(m.z[d, i] for i in m.I if food_group_of.get(i) == g) + m.slack_group[d, g]
                >= targets[g]
            )

        model.group_min = Constraint(model.D, model.G, rule=group_rule)

    if options.enforce_weekly_groups:
        group_portions = prepared.group_portions

        def weekly_group_rule(m: Any, g: str) -> Any:
            target = opt.weekly_group_portions_min.get(g)
            if target is None:
                target = float(targets.get(g, 0)) * len(prepared.days)
            return (
                sum(
                    group_portions[(r, g)] * m.x[r, d, meal]
                    for r in m.R
                    for d in m.D
                    for meal in m.M
                )
                + m.slack_weekly_group[g]
                >= target
            )

        model.weekly_group_min = Constraint(model.G, rule=weekly_group_rule)

    if options.enforce_daily_kcal and opt.calories_daily_min is not None:
        kcal = prepared.kcal

        def cal_min_rule(m: Any, d: int) -> Any:
            return (
                sum(kcal[r] * m.x[r, d, meal] for r in m.R for meal in m.M) + m.slack_cal_min[d]
                >= opt.calories_daily_min
            )

        model.cal_min = Constraint(model.D, rule=cal_min_rule)

    if options.enforce_daily_kcal and opt.calories_daily_max is not None:
        kcal_max = prepared.kcal

        def cal_max_rule(m: Any, d: int) -> Any:
            return (
                sum(kcal_max[r] * m.x[r, d, meal] for r in m.R for meal in m.M) - m.slack_cal_max[d]
                <= opt.calories_daily_max
            )

        model.cal_max = Constraint(model.D, rule=cal_max_rule)

    if options.enforce_daily_fiber and opt.fiber_daily_min is not None:
        fiber = prepared.fiber

        def fiber_min_rule(m: Any, d: int) -> Any:
            return (
                sum(fiber[r] * m.x[r, d, meal] for r in m.R for meal in m.M) + m.slack_fiber_min[d]
                >= opt.fiber_daily_min
            )

        model.fiber_min = Constraint(model.D, rule=fiber_min_rule)

    if options.enforce_daily_fiber and opt.fiber_daily_max is not None:
        fiber_d_max = prepared.fiber

        def fiber_max_rule(m: Any, d: int) -> Any:
            return (
                sum(fiber_d_max[r] * m.x[r, d, meal] for r in m.R for meal in m.M)
                - m.slack_fiber_max[d]
                <= opt.fiber_daily_max
            )

        model.fiber_max = Constraint(model.D, rule=fiber_max_rule)

    if options.enforce_daily_protein and opt.protein_daily_min is not None:
        protein = prepared.protein

        def protein_min_rule(m: Any, d: int) -> Any:
            return (
                sum(protein[r] * m.x[r, d, meal] for r in m.R for meal in m.M)
                + m.slack_protein_min[d]
                >= opt.protein_daily_min
            )

        model.protein_min = Constraint(model.D, rule=protein_min_rule)

    if options.enforce_daily_protein and opt.protein_daily_max is not None:
        protein_d_max = prepared.protein

        def protein_max_rule(m: Any, d: int) -> Any:
            return (
                sum(protein_d_max[r] * m.x[r, d, meal] for r in m.R for meal in m.M)
                - m.slack_protein_max[d]
                <= opt.protein_daily_max
            )

        model.protein_max = Constraint(model.D, rule=protein_max_rule)

    if options.enforce_weekly_kcal and opt.calories_weekly_min is not None:
        kcal_w = prepared.kcal

        def weekly_cal_min(m: Any) -> Any:
            return (
                sum(kcal_w[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M)
                + m.slack_weekly_cal_min
                >= opt.calories_weekly_min
            )

        model.weekly_cal_min = Constraint(rule=weekly_cal_min)

    if options.enforce_weekly_kcal and opt.calories_weekly_max is not None:
        kcal_wm = prepared.kcal

        def weekly_cal_max(m: Any) -> Any:
            return (
                sum(kcal_wm[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M)
                - m.slack_weekly_cal_max
                <= opt.calories_weekly_max
            )

        model.weekly_cal_max = Constraint(rule=weekly_cal_max)

    if options.enforce_weekly_fiber and opt.fiber_weekly_min is not None:
        fiber_w = prepared.fiber

        def weekly_fiber_min(m: Any) -> Any:
            return (
                sum(fiber_w[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M)
                + m.slack_weekly_fiber
                >= opt.fiber_weekly_min
            )

        model.weekly_fiber_min = Constraint(rule=weekly_fiber_min)

    if options.enforce_weekly_protein and opt.protein_weekly_min is not None:
        protein_w = prepared.protein

        def weekly_protein_min(m: Any) -> Any:
            return (
                sum(protein_w[r] * m.x[r, d, meal] for r in m.R for d in m.D for meal in m.M)
                + m.slack_weekly_protein
                >= opt.protein_weekly_min
            )

        model.weekly_protein_min = Constraint(rule=weekly_protein_min)

    if spacing_active:

        def pair_lower_d1(m: Any, r: int, d1: int, d2: int) -> Any:
            return m.recipe_pair[r, d1, d2] <= sum(m.x[r, d1, meal] for meal in m.M)

        def pair_lower_d2(m: Any, r: int, d1: int, d2: int) -> Any:
            return m.recipe_pair[r, d1, d2] <= sum(m.x[r, d2, meal] for meal in m.M)

        def pair_upper(m: Any, r: int, d1: int, d2: int) -> Any:
            return (
                m.recipe_pair[r, d1, d2]
                >= sum(m.x[r, d1, meal] for meal in m.M) + sum(m.x[r, d2, meal] for meal in m.M) - 1
            )

        model.pair_lower_d1 = Constraint(model.R, model.PAIRS, rule=pair_lower_d1)
        model.pair_lower_d2 = Constraint(model.R, model.PAIRS, rule=pair_lower_d2)
        model.pair_upper = Constraint(model.R, model.PAIRS, rule=pair_upper)

    rating = prepared.rating
    recency = prepared.recency

    def objective_rule(m: Any) -> Any:
        diversity = sum(m.y[i] for i in m.I)
        rating_term = sum(
            rating[r] * sum(m.x[r, d, meal] for d in m.D for meal in m.M) for r in m.R
        )
        recency_term = sum(
            recency[r] * sum(m.x[r, d, meal] for d in m.D for meal in m.M) for r in m.R
        )
        slack = sum(m.slack_group[d, g] for d in m.D for g in m.G) + sum(
            m.slack_weekly_group[g] for g in m.G
        )
        for attr in (
            "slack_cal_min",
            "slack_cal_max",
            "slack_fiber_min",
            "slack_fiber_max",
            "slack_protein_min",
            "slack_protein_max",
            "slack_snack",
        ):
            if hasattr(m, attr):
                slack += sum(getattr(m, attr)[d] for d in m.D)
        for attr in (
            "slack_weekly_cal_min",
            "slack_weekly_cal_max",
            "slack_weekly_fiber",
            "slack_weekly_protein",
        ):
            if hasattr(m, attr):
                slack += getattr(m, attr)
        spacing_term: Any = 0
        if spacing_active:
            spacing_term = sum(
                penalty_by_gap.get(d2 - d1, 0.0) * m.recipe_pair[r, d1, d2]
                for r in m.R
                for d1, d2 in relevant_pairs
            )
        return (
            opt.diversity_weight * diversity
            + opt.rating_weight * rating_term
            - opt.recency_weight * recency_term
            - opt.slack_weight * slack
            - opt.spacing_weight * spacing_term
        )

    model.objective = Objective(rule=objective_rule, sense=maximize)
    return model


def variable_count(prepared: PreparedData) -> int:
    return (
        len(prepared.recipes) * len(prepared.days) * len(prepared.meal_types)
        + len(prepared.days) * len(prepared.ingredients_canonical)
        + len(prepared.ingredients_canonical)
    )


def total_slack(model: Any, prepared: PreparedData) -> float:
    total = 0.0
    for d in prepared.days:
        for g in prepared.food_groups:
            value = cast(Any, model.slack_group[d, g]).value
            total += float(value or 0)
    for g in prepared.food_groups:
        value = cast(Any, model.slack_weekly_group[g]).value
        total += float(value or 0)
    for attr in (
        "slack_cal_min",
        "slack_cal_max",
        "slack_fiber_min",
        "slack_fiber_max",
        "slack_protein_min",
        "slack_protein_max",
        "slack_snack",
    ):
        if hasattr(model, attr):
            for d in prepared.days:
                value = cast(Any, getattr(model, attr)[d]).value
                total += float(value or 0)
    for attr in (
        "slack_weekly_cal_min",
        "slack_weekly_cal_max",
        "slack_weekly_fiber",
        "slack_weekly_protein",
    ):
        if hasattr(model, attr):
            value = cast(Any, getattr(model, attr)).value
            total += float(value or 0)
    return total
