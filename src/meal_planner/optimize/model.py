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
from meal_planner.optimize.data import PreparedData, ProfileSpec


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
    enforce_leftover_pairing: bool = True


def _slot_user_keys(prepared: PreparedData) -> list[tuple[str, str]]:
    return [(p.name, m) for p in prepared.profiles for m in prepared.per_user_meal_types]


def _meal_appearances(m: Any, r: int, d: int, prepared: PreparedData) -> Any:
    shared_sum = sum(m.x_shared[r, d, meal] for meal in prepared.shared_meal_types)
    user_sum = sum(
        m.x_user[p.name, r, d, meal]
        for p in prepared.profiles
        for meal in prepared.per_user_meal_types
    )
    return shared_sum + user_sum


def _user_recipes_on_day(m: Any, p: ProfileSpec, r: int, d: int, prepared: PreparedData) -> Any:
    shared_sum = sum(m.x_shared[r, d, meal] for meal in prepared.shared_meal_types)
    user_sum = sum(m.x_user[p.name, r, d, meal] for meal in prepared.per_user_meal_types)
    return shared_sum + user_sum


def _user_servings_on_day(m: Any, p: ProfileSpec, r: int, d: int, prepared: PreparedData) -> Any:
    """How many servings of recipe r profile p eats on day d.

    Same as _user_recipes_on_day except a shared dish can be a fraction of a
    serving when the profile's shared_portion range allows it, so one dish can
    feed people on very different calorie targets. Used for nutrition only:
    Daily Dozen and ingredient variety stay dish-based, since a smaller plate
    of a curry still delivers the same distinct foods.
    """
    if p.portion_is_flexible and prepared.shared_meal_types:
        shared_sum = sum(
            m.share[p.name, r, d, meal]
            for meal in prepared.shared_meal_types
            if prepared.allowed_meal[(r, meal)]
        )
    else:
        shared_sum = sum(m.x_shared[r, d, meal] for meal in prepared.shared_meal_types)
    user_sum = sum(m.x_user[p.name, r, d, meal] for meal in prepared.per_user_meal_types)
    return shared_sum + user_sum


def build_model(prepared: PreparedData, settings: Settings, options: ModelOptions) -> Any:
    opt = settings.optimizer
    targets = settings.daily_dozen_targets
    profile_names = [p.name for p in prepared.profiles]

    model = ConcreteModel()
    model.D = Set(initialize=prepared.days)
    model.M = Set(initialize=prepared.meal_types)
    model.R = Set(initialize=prepared.recipes)
    model.I = Set(initialize=prepared.ingredients_canonical)
    model.G = Set(initialize=prepared.food_groups)
    model.SHARED_M = Set(initialize=prepared.shared_meal_types)
    model.USER_M = Set(initialize=prepared.per_user_meal_types)
    model.P = Set(initialize=profile_names)

    if prepared.shared_meal_types:
        model.x_shared = Var(model.R, model.D, model.SHARED_M, domain=Binary)
    if prepared.per_user_meal_types:
        model.x_user = Var(model.P, model.R, model.D, model.USER_M, domain=Binary)

    # How much of a shared dish each person eats, in servings. Only profiles
    # with a range wider than a point need a variable; everyone else eats the
    # full serving and the x_shared binary already says so.
    flexible_profiles = [p for p in prepared.profiles if p.portion_is_flexible]
    portion_bounds = {
        p.name: (p.shared_portion_min, p.shared_portion_max) for p in flexible_profiles
    }
    # Only recipes the slot can actually take get a variable — most of the
    # catalogue isn't lunch- or dinner-capable, and x_shared would pin those to
    # zero anyway.
    share_keys = [
        (p.name, r, d, meal)
        for p in flexible_profiles
        for meal in prepared.shared_meal_types
        for r in prepared.recipes
        if prepared.allowed_meal[(r, meal)]
        for d in prepared.days
    ]
    if share_keys:
        model.SHARE_KEYS = Set(initialize=share_keys, dimen=4)
        model.share = Var(
            model.SHARE_KEYS,
            domain=NonNegativeReals,
            bounds=(0, max(p.shared_portion_max for p in flexible_profiles)),
        )

    model.z = Var(model.P, model.D, model.I, domain=Binary)
    model.y = Var(model.P, model.I, domain=Binary)

    # Per-person whey scoops the solver may allocate to hit the protein floor
    # within the calorie band (counts toward both protein and calories).
    whey_enabled = settings.topup.enabled and bool(profile_names)
    if whey_enabled:
        # Continuous for solver speed; rounded to whole scoops at extraction.
        model.whey = Var(
            model.P,
            model.D,
            domain=NonNegativeReals,
            bounds=(0, settings.topup.max_whey_scoops),
        )

    model.slack_group = Var(model.P, model.D, model.G, domain=NonNegativeReals)
    model.slack_weekly_group = Var(model.P, model.G, domain=NonNegativeReals)

    profiles_by_name = {p.name: p for p in prepared.profiles}

    profiles_needing = {
        "slack_cal_min": [
            p.name
            for p in prepared.profiles
            if options.enforce_daily_kcal and p.calories_daily_min is not None
        ],
        "slack_cal_max": [
            p.name
            for p in prepared.profiles
            if options.enforce_daily_kcal and p.calories_daily_max is not None
        ],
        "slack_fiber_min": [
            p.name
            for p in prepared.profiles
            if options.enforce_daily_fiber and p.fiber_daily_min is not None
        ],
        "slack_protein_min": [
            p.name
            for p in prepared.profiles
            if options.enforce_daily_protein and p.protein_daily_min is not None
        ],
        "slack_protein_max": [
            p.name
            for p in prepared.profiles
            if options.enforce_daily_protein and p.protein_daily_max is not None
        ],
    }
    for attr, names in profiles_needing.items():
        if names:
            setattr(
                model,
                attr,
                Var(Set(initialize=names), model.D, domain=NonNegativeReals),
            )

    if options.enforce_weekly_kcal and opt.calories_weekly_min is not None:
        model.slack_weekly_cal_min = Var(domain=NonNegativeReals)
    if options.enforce_weekly_kcal and opt.calories_weekly_max is not None:
        model.slack_weekly_cal_max = Var(domain=NonNegativeReals)
    if options.enforce_weekly_fiber and opt.fiber_weekly_min is not None:
        model.slack_weekly_fiber = Var(domain=NonNegativeReals)
    if options.enforce_weekly_protein and opt.protein_weekly_min is not None:
        model.slack_weekly_protein = Var(domain=NonNegativeReals)
    if opt.snack_optional and "snack" in prepared.meal_types:
        snack_slot_keys = _build_snack_keys(prepared)
        if snack_slot_keys:
            model.SNACK_KEYS = Set(initialize=snack_slot_keys, dimen=3)
            model.slack_snack = Var(model.SNACK_KEYS, domain=NonNegativeReals)

    pairs = [(d1, d2) for d1 in prepared.days for d2 in prepared.days if d1 < d2]
    penalty_by_gap = opt.spacing_penalty_by_gap or {}
    relevant_pairs = [(d1, d2) for d1, d2 in pairs if penalty_by_gap.get(d2 - d1, 0.0) > 0]
    spacing_active = opt.spacing_weight > 0 and bool(relevant_pairs) and opt.max_recipe_repeats > 1
    if spacing_active:
        model.PAIRS = Set(initialize=relevant_pairs, dimen=2)
        model.recipe_pair = Var(model.R, model.PAIRS, domain=Binary)
        # appears_on_day[r, d] = 1 iff recipe r is used by anyone on day d.
        # Lets recipe_pair stay binary even when multiple users eat r on the same day.
        model.appears_on_day = Var(model.R, model.D, domain=Binary)

    snack_optional = opt.snack_optional and "snack" in prepared.meal_types
    allowed_meal = prepared.allowed_meal

    def shared_slot_rule(m: Any, d: int, meal: str) -> Any:
        return sum(m.x_shared[r, d, meal] for r in m.R) == 1

    if prepared.shared_meal_types:
        model.shared_slot = Constraint(model.D, model.SHARED_M, rule=shared_slot_rule)

        def shared_allowed(m: Any, r: int, d: int, meal: str) -> Any:
            return m.x_shared[r, d, meal] <= allowed_meal[(r, meal)]

        model.shared_allowed = Constraint(model.R, model.D, model.SHARED_M, rule=shared_allowed)

        if share_keys:
            # Pin share to 0 for dishes not cooked, and into [min, max] for the
            # one that is. Exact because x_shared is binary.
            def share_upper_rule(m: Any, p: str, r: int, d: int, meal: str) -> Any:
                return m.share[p, r, d, meal] <= portion_bounds[p][1] * m.x_shared[r, d, meal]

            def share_lower_rule(m: Any, p: str, r: int, d: int, meal: str) -> Any:
                return m.share[p, r, d, meal] >= portion_bounds[p][0] * m.x_shared[r, d, meal]

            model.share_upper = Constraint(model.SHARE_KEYS, rule=share_upper_rule)
            model.share_lower = Constraint(model.SHARE_KEYS, rule=share_lower_rule)

            # Exactly one dish fills the slot, so the shares across recipes must
            # add to one person's portion. Implied at integrality but not in the
            # relaxation, where it stops the solver splitting a portion across
            # fractional dishes — which is what made this slow to prove.
            flex_slots = [
                (p.name, d, meal)
                for p in flexible_profiles
                for d in prepared.days
                for meal in prepared.shared_meal_types
            ]
            model.FLEX_SLOTS = Set(initialize=flex_slots, dimen=3)

            def slot_share_total(m: Any, p: str, d: int, meal: str) -> Any:
                return sum(
                    m.share[p, r, d, meal]
                    for r in prepared.recipes
                    if prepared.allowed_meal[(r, meal)]
                )

            def slot_share_min(m: Any, p: str, d: int, meal: str) -> Any:
                return slot_share_total(m, p, d, meal) >= portion_bounds[p][0]

            def slot_share_max(m: Any, p: str, d: int, meal: str) -> Any:
                return slot_share_total(m, p, d, meal) <= portion_bounds[p][1]

            model.slot_share_min = Constraint(model.FLEX_SLOTS, rule=slot_share_min)
            model.slot_share_max = Constraint(model.FLEX_SLOTS, rule=slot_share_max)

    snack_slot_set = set(prepared.snack_meal_types)

    if prepared.per_user_meal_types:

        def user_slot_rule(m: Any, p: str, d: int, meal: str) -> Any:
            expr = sum(m.x_user[p, r, d, meal] for r in m.R)
            if meal in snack_slot_set:
                # Each extra snack slot is optional — fill 0 or 1 recipe.
                return expr <= 1
            if snack_optional and meal == "snack" and hasattr(m, "slack_snack"):
                return expr + m.slack_snack[p, d, meal] == 1
            return expr == 1

        model.user_slot = Constraint(model.P, model.D, model.USER_M, rule=user_slot_rule)

        def user_allowed(m: Any, p: str, r: int, d: int, meal: str) -> Any:
            return m.x_user[p, r, d, meal] <= allowed_meal[(r, meal)]

        model.user_allowed = Constraint(model.P, model.R, model.D, model.USER_M, rule=user_allowed)

        # Pin per-profile fixed meals (e.g. Matt's breakfast smoothie every day).
        fixed_keys = [
            (p, meal, d) for (p, meal) in prepared.fixed_assignments for d in prepared.days
        ]
        if fixed_keys:
            model.FIXED = Set(initialize=fixed_keys, dimen=3)

            def fixed_rule(m: Any, p: str, meal: str, d: int) -> Any:
                r = prepared.fixed_assignments[(p, meal)]
                return m.x_user[p, r, d, meal] == 1

            model.fixed_meal = Constraint(model.FIXED, rule=fixed_rule)

        # Cap how many of the day's snacks come from a given category, e.g.
        # at most one smoothie among the snack slots.
        recipes_set = set(prepared.recipes)
        cap_categories = [
            cat
            for cat in prepared.snack_category_limits
            if snack_slot_set and prepared.category_recipe_ids.get(cat)
        ]
        if cap_categories:
            cap_keys = [
                (p, d, cat) for p in profile_names for d in prepared.days for cat in cap_categories
            ]
            model.SNACK_CAP = Set(initialize=cap_keys, dimen=3)

            def snack_category_rule(m: Any, p: str, d: int, cat: str) -> Any:
                limit = prepared.snack_category_limits[cat]
                rids = prepared.category_recipe_ids.get(cat, set()) & recipes_set
                return (
                    sum(m.x_user[p, r, d, slot] for slot in snack_slot_set for r in rids) <= limit
                )

            model.snack_category_cap = Constraint(model.SNACK_CAP, rule=snack_category_rule)

    def repeat_rule(m: Any, r: int) -> Any:
        # Fixed meals are pinned every day by design — exempt from the repeat cap.
        if r in prepared.fixed_recipe_ids:
            return Constraint.Skip
        return sum(_meal_appearances(m, r, d, prepared) for d in m.D) <= opt.max_recipe_repeats

    model.repeat_limit = Constraint(model.R, rule=repeat_rule)

    # User-pinned recipes that must appear at least once during the week.
    must_ids = [r for r in opt.must_include_recipe_ids if r in set(prepared.recipes)]
    if must_ids:

        def must_include_rule(m: Any, r: int) -> Any:
            return sum(_meal_appearances(m, r, d, prepared) for d in m.D) >= 1

        model.must_include = Constraint(Set(initialize=must_ids), rule=must_include_rule)

    # Batch cooking / leftovers: each shared lunch/dinner dish that is used appears
    # exactly twice (cook fresh once, eat as leftovers once). Needs an even number
    # of days so the slots tile into pairs; gated so it can be relaxed away.
    leftover_active = (
        opt.leftover_pairing
        and options.enforce_leftover_pairing
        and bool(prepared.shared_meal_types)
        and len(prepared.days) % 2 == 0
    )
    if leftover_active:
        leftover_keys = [(r, meal) for r in prepared.recipes for meal in prepared.shared_meal_types]
        model.LEFTOVER = Set(initialize=leftover_keys, dimen=2)
        model.leftover_used = Var(model.LEFTOVER, domain=Binary)

        def leftover_rule(m: Any, r: int, meal: str) -> Any:
            return sum(m.x_shared[r, d, meal] for d in m.D) == 2 * m.leftover_used[r, meal]

        model.leftover_pairing = Constraint(model.LEFTOVER, rule=leftover_rule)

    def one_per_day_rule(m: Any, p: str, r: int, d: int) -> Any:
        profile = profiles_by_name[p]
        return _user_recipes_on_day(m, profile, r, d, prepared) <= 1

    model.one_recipe_per_day = Constraint(model.P, model.R, model.D, rule=one_per_day_rule)

    portion_met = prepared.portion_met

    def ingredient_use_rule(m: Any, p: str, d: int, i: str) -> Any:
        profile = profiles_by_name[p]
        return m.z[p, d, i] <= sum(
            portion_met[(r, i)] * _user_recipes_on_day(m, profile, r, d, prepared) for r in m.R
        )

    model.ingredient_use = Constraint(model.P, model.D, model.I, rule=ingredient_use_rule)

    def ingredient_lower(m: Any, p: str, i: str) -> Any:
        return m.y[p, i] <= sum(m.z[p, d, i] for d in m.D)

    def ingredient_upper(m: Any, p: str, i: str) -> Any:
        return sum(m.z[p, d, i] for d in m.D) <= len(prepared.days) * m.y[p, i]

    model.ingredient_global_lower = Constraint(model.P, model.I, rule=ingredient_lower)
    model.ingredient_global_upper = Constraint(model.P, model.I, rule=ingredient_upper)

    if options.enforce_group_targets:
        food_group_of = prepared.food_group_of

        def group_rule(m: Any, p: str, d: int, g: str) -> Any:
            return (
                sum(m.z[p, d, i] for i in m.I if food_group_of.get(i) == g) + m.slack_group[p, d, g]
                >= targets[g]
            )

        model.group_min = Constraint(model.P, model.D, model.G, rule=group_rule)

    if options.enforce_weekly_groups:
        group_portions = prepared.group_portions

        def weekly_group_rule(m: Any, p: str, g: str) -> Any:
            target = opt.weekly_group_portions_min.get(g)
            if target is None:
                target = float(targets.get(g, 0)) * len(prepared.days)
            profile = profiles_by_name[p]
            user_view = sum(
                group_portions[(r, g)] * _user_recipes_on_day(m, profile, r, d, prepared)
                for r in m.R
                for d in m.D
            )
            return user_view + m.slack_weekly_group[p, g] >= target

        model.weekly_group_min = Constraint(model.P, model.G, rule=weekly_group_rule)

    def _whey_kcal(m: Any, p: str, d: int) -> Any:
        return m.whey[p, d] * settings.topup.whey_kcal if whey_enabled else 0

    def _whey_protein(m: Any, p: str, d: int) -> Any:
        return m.whey[p, d] * settings.topup.whey_protein_g if whey_enabled else 0

    if options.enforce_daily_kcal:
        kcal = prepared.kcal

        def cal_min_rule(m: Any, p: str, d: int) -> Any:
            profile = profiles_by_name[p]
            # Whey deliberately does NOT count toward the calorie floor: it is a
            # protein supplement, not a way to make up calories. Counting it here
            # let the solver max out scoops purely to fill the floor, which drove
            # protein far above target. It still counts toward the ceiling below.
            return (
                sum(kcal[r] * _user_servings_on_day(m, profile, r, d, prepared) for r in m.R)
                + m.slack_cal_min[p, d]
                >= profile.calories_daily_min
            )

        if profiles_needing["slack_cal_min"]:
            model.cal_min = Constraint(
                Set(initialize=profiles_needing["slack_cal_min"]), model.D, rule=cal_min_rule
            )

        def cal_max_rule(m: Any, p: str, d: int) -> Any:
            profile = profiles_by_name[p]
            return (
                sum(kcal[r] * _user_servings_on_day(m, profile, r, d, prepared) for r in m.R)
                + _whey_kcal(m, p, d)
                - m.slack_cal_max[p, d]
                <= profile.calories_daily_max
            )

        if profiles_needing["slack_cal_max"]:
            model.cal_max = Constraint(
                Set(initialize=profiles_needing["slack_cal_max"]), model.D, rule=cal_max_rule
            )

    if options.enforce_daily_fiber:
        fiber = prepared.fiber

        def fiber_min_rule(m: Any, p: str, d: int) -> Any:
            profile = profiles_by_name[p]
            return (
                sum(fiber[r] * _user_servings_on_day(m, profile, r, d, prepared) for r in m.R)
                + m.slack_fiber_min[p, d]
                >= profile.fiber_daily_min
            )

        if profiles_needing["slack_fiber_min"]:
            model.fiber_min = Constraint(
                Set(initialize=profiles_needing["slack_fiber_min"]), model.D, rule=fiber_min_rule
            )

    if options.enforce_daily_protein:
        protein = prepared.protein

        def protein_min_rule(m: Any, p: str, d: int) -> Any:
            profile = profiles_by_name[p]
            return (
                sum(protein[r] * _user_servings_on_day(m, profile, r, d, prepared) for r in m.R)
                + _whey_protein(m, p, d)
                + m.slack_protein_min[p, d]
                >= profile.protein_daily_min
            )

        if profiles_needing["slack_protein_min"]:
            model.protein_min = Constraint(
                Set(initialize=profiles_needing["slack_protein_min"]),
                model.D,
                rule=protein_min_rule,
            )

        def protein_max_rule(m: Any, p: str, d: int) -> Any:
            profile = profiles_by_name[p]
            return (
                sum(protein[r] * _user_servings_on_day(m, profile, r, d, prepared) for r in m.R)
                + _whey_protein(m, p, d)
                - m.slack_protein_max[p, d]
                <= profile.protein_daily_max
            )

        if profiles_needing["slack_protein_max"]:
            model.protein_max = Constraint(
                Set(initialize=profiles_needing["slack_protein_max"]),
                model.D,
                rule=protein_max_rule,
            )

    if options.enforce_weekly_kcal and opt.calories_weekly_min is not None:
        kcal_w = prepared.kcal
        cal_min_target = opt.calories_weekly_min * len(prepared.profiles)

        def weekly_cal_min(m: Any) -> Any:
            total = sum(
                kcal_w[r] * _user_servings_on_day(m, p, r, d, prepared)
                for p in prepared.profiles
                for r in m.R
                for d in m.D
            )
            return total + m.slack_weekly_cal_min >= cal_min_target

        model.weekly_cal_min = Constraint(rule=weekly_cal_min)

    if options.enforce_weekly_kcal and opt.calories_weekly_max is not None:
        kcal_wm = prepared.kcal
        cal_max_target = opt.calories_weekly_max * len(prepared.profiles)

        def weekly_cal_max(m: Any) -> Any:
            total = sum(
                kcal_wm[r] * _user_servings_on_day(m, p, r, d, prepared)
                for p in prepared.profiles
                for r in m.R
                for d in m.D
            )
            return total - m.slack_weekly_cal_max <= cal_max_target

        model.weekly_cal_max = Constraint(rule=weekly_cal_max)

    if options.enforce_weekly_fiber and opt.fiber_weekly_min is not None:
        fiber_w = prepared.fiber
        fiber_target = opt.fiber_weekly_min * len(prepared.profiles)

        def weekly_fiber_min(m: Any) -> Any:
            total = sum(
                fiber_w[r] * _user_servings_on_day(m, p, r, d, prepared)
                for p in prepared.profiles
                for r in m.R
                for d in m.D
            )
            return total + m.slack_weekly_fiber >= fiber_target

        model.weekly_fiber_min = Constraint(rule=weekly_fiber_min)

    if options.enforce_weekly_protein and opt.protein_weekly_min is not None:
        protein_w = prepared.protein
        protein_target = opt.protein_weekly_min * len(prepared.profiles)

        def weekly_protein_min(m: Any) -> Any:
            total = sum(
                protein_w[r] * _user_servings_on_day(m, p, r, d, prepared)
                for p in prepared.profiles
                for r in m.R
                for d in m.D
            )
            return total + m.slack_weekly_protein >= protein_target

        model.weekly_protein_min = Constraint(rule=weekly_protein_min)

    if spacing_active:
        max_per_day = max(
            1,
            len(prepared.shared_meal_types)
            + len(prepared.profiles) * len(prepared.per_user_meal_types),
        )

        def appears_link(m: Any, r: int, d: int) -> Any:
            # appears_on_day is 1 iff any slot is filled with r on day d.
            # Using a big-M-style upper bound keeps it binary even when multiple
            # users eat the same recipe on the same day.
            return _meal_appearances(m, r, d, prepared) <= max_per_day * m.appears_on_day[r, d]

        model.appears_link = Constraint(model.R, model.D, rule=appears_link)

        def pair_upper(m: Any, r: int, d1: int, d2: int) -> Any:
            return m.recipe_pair[r, d1, d2] >= (
                m.appears_on_day[r, d1] + m.appears_on_day[r, d2] - 1
            )

        model.pair_upper = Constraint(model.R, model.PAIRS, rule=pair_upper)

    rating = prepared.rating
    recency = prepared.recency

    def objective_rule(m: Any) -> Any:
        diversity = sum(m.y[p, i] for p in m.P for i in m.I)
        rating_term = sum(
            rating[r] * _meal_appearances(m, r, d, prepared) for r in m.R for d in m.D
        )
        recency_term = sum(
            recency[r] * _meal_appearances(m, r, d, prepared) for r in m.R for d in m.D
        )
        slack = sum(m.slack_group[p, d, g] for p in m.P for d in m.D for g in m.G) + sum(
            m.slack_weekly_group[p, g] for p in m.P for g in m.G
        )
        per_profile_per_day_slacks = (
            "slack_cal_min",
            "slack_cal_max",
            "slack_fiber_min",
            "slack_protein_min",
            "slack_protein_max",
        )
        for attr in per_profile_per_day_slacks:
            if hasattr(m, attr):
                var = getattr(m, attr)
                slack += sum(var[idx] for idx in var)
        if hasattr(m, "slack_snack"):
            slack += sum(m.slack_snack[idx] for idx in m.slack_snack)
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
                if r not in prepared.fixed_recipe_ids
                for d1, d2 in relevant_pairs
            )
        whey_term: Any = 0
        if whey_enabled:
            whey_term = sum(m.whey[p, d] for p in m.P for d in m.D)
        return (
            opt.diversity_weight * diversity
            + opt.rating_weight * rating_term
            - opt.recency_weight * recency_term
            - opt.slack_weight * slack
            - opt.spacing_weight * spacing_term
            - settings.topup.whey_solver_penalty * whey_term
        )

    model.objective = Objective(rule=objective_rule, sense=maximize)
    return model


def _build_snack_keys(prepared: PreparedData) -> list[tuple[str, int, str]]:
    return [(p.name, d, "snack") for p in prepared.profiles for d in prepared.days]


def variable_count(prepared: PreparedData) -> int:
    shared = len(prepared.recipes) * len(prepared.days) * len(prepared.shared_meal_types)
    user = (
        len(prepared.profiles)
        * len(prepared.recipes)
        * len(prepared.days)
        * len(prepared.per_user_meal_types)
    )
    z = len(prepared.profiles) * len(prepared.days) * len(prepared.ingredients_canonical)
    y = len(prepared.profiles) * len(prepared.ingredients_canonical)
    return shared + user + z + y


def total_slack(model: Any, prepared: PreparedData) -> float:
    total = 0.0
    for p in prepared.profiles:
        for d in prepared.days:
            for g in prepared.food_groups:
                value = cast(Any, model.slack_group[p.name, d, g]).value
                total += float(value or 0)
        for g in prepared.food_groups:
            value = cast(Any, model.slack_weekly_group[p.name, g]).value
            total += float(value or 0)
    for attr in (
        "slack_cal_min",
        "slack_cal_max",
        "slack_fiber_min",
        "slack_protein_min",
        "slack_protein_max",
    ):
        if hasattr(model, attr):
            var = getattr(model, attr)
            for idx in var:
                total += float(cast(Any, var[idx]).value or 0)
    if hasattr(model, "slack_snack"):
        var = model.slack_snack
        for idx in var:
            total += float(cast(Any, var[idx]).value or 0)
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
