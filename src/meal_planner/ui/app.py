from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import cast

import streamlit as st

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
from meal_planner.correlation import new_correlation_id, set_correlation_id
from meal_planner.db import get_engine, wait_for_db
from meal_planner.logging import configure as configure_logging
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.ui.charts import (
    daily_dozen_heatmap,
    daily_dozen_weekly_chart,
    daily_macros_chart,
    day_meal_stack_chart,
    dozen_summary_counts,
    recipe_frequency_chart,
)
from meal_planner.ui.data import (
    DayPlan,
    DayPlanForProfile,
    IngredientLine,
    MealEntry,
    PlanView,
    load_latest_plan_view,
    load_plan_view,
)

LOW_CONFIDENCE_MATCH = 90.0

ALL_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
DEFAULT_SHARED = ("lunch", "dinner")


def _bootstrap_settings(config_path: Path) -> Settings:
    return Settings.load(config_path)


def _format_macro(meal: MealEntry) -> str:
    return (
        f"{meal.kcal:.0f} kcal · "
        f"{meal.protein_g:.0f} g protein · "
        f"{meal.fiber_g:.0f} g fibre · "
        f"{meal.fat_g:.0f} g fat · "
        f"{meal.carbs_g:.0f} g carbs"
    )


def _format_gap(label: str, value: float, unit: str) -> str:
    return f"{label}: ~{value:.0f} {unit}"


def _meal_label(meal_type: str) -> str:
    if meal_type.startswith("snack_"):
        return f"Snack {meal_type.split('_', 1)[1]}"
    return meal_type.title()


_MEAL_ORDER = ["breakfast", "lunch", "dinner", "snack", "snack_1", "snack_2", "snack_3", "snack_4"]


def _ordered_meal_types(view: PlanView) -> list[str]:
    present = {
        meal.meal_type for day in view.days for prof in day.per_profile for meal in prof.meals
    }
    ordered = [m for m in _MEAL_ORDER if m in present]
    ordered.extend(sorted(m for m in present if m not in ordered))
    return ordered


# Daily Dozen category icons (emoji stand-ins for category images).
DOZEN_ICONS: dict[str, str] = {
    "Beans": "🫘",
    "Berries": "🫐",
    "Other Fruits": "🍎",
    "Cruciferous Vegetables": "🥦",
    "Greens": "🥬",
    "Other Vegetables": "🥕",
    "Flaxseeds or Linseeds": "🟤",
    "Nuts and Seeds": "🥜",
    "Herbs and Spices": "🌿",
    "Whole Grains": "🌾",
}
_DOZEN_ORDER = list(DOZEN_ICONS)


def _week_commencing(d: date) -> str:
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("w/c %d %b %Y")


def _render_dozen_strip(daily_dozen: dict[str, tuple[int, int, float]]) -> None:
    if not daily_dozen:
        return
    chips: list[str] = []
    for group in _DOZEN_ORDER:
        triple = daily_dozen.get(group)
        if triple is None:
            continue
        count, target, _portions = triple
        icon = DOZEN_ICONS.get(group, "•")
        if target > 0 and count >= target:
            color = "#1a7f37"  # met — green
        elif count > 0:
            color = "#bf8700"  # partial — amber
        else:
            color = "#9aa0a6"  # missed — grey
        chips.append(
            f"<span title='{group}' style='display:inline-block;margin:0 12px 4px 0;"
            f"white-space:nowrap;color:{color};font-size:0.95em'>"
            f"{icon} {count}/{target}</span>"
        )
    st.markdown(
        "<div style='line-height:1.9'>" + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )


def _stars(rating: float | None) -> str:
    if rating is None:
        return ""
    rounded = max(0, min(5, round(rating)))
    return "★" * rounded + "☆" * (5 - rounded) + f" ({rating:.1f})"


def _last_eaten_caption(last_eaten: date | None) -> str:
    if last_eaten is None:
        return "First appearance in your recorded plans."
    return f"Last planned for: {last_eaten.isoformat()}"


def _render_ingredient_table(ingredients: list[IngredientLine], meal_kcal: float) -> None:
    rows = []
    flagged: list[str] = []
    for ing in ingredients:
        grams_disp = f"{ing.per_serving_grams:.0f}" if ing.per_serving_grams is not None else "—"
        match_text = ing.match_source_name or "—"
        if (
            ing.match_score is not None
            and ing.match_source_name is not None
            and (ing.source != "sub_recipe")
        ):
            match_text += f" ({ing.match_score:.0f})"
        if ing.source:
            match_text += f" · {ing.source}"
        warn = []
        if ing.per_serving_grams is None:
            warn.append("no grams")
        if ing.source != "sub_recipe":
            if ing.match_score is not None and ing.match_score < LOW_CONFIDENCE_MATCH:
                warn.append("low-confidence match")
            if ing.match_source_name is None and ing.ingredient_canonical is not None:
                warn.append("no nutrition match")
        if ing.sub_recipe_id is not None and ing.source != "sub_recipe":
            warn.append("sub-recipe not yet expanded")
        if warn:
            flagged.append(f"`{ing.raw_text}` — {', '.join(warn)}")
        rows.append(
            {
                "Ingredient": ing.raw_text,
                "g/serving": grams_disp,
                "kcal": f"{ing.kcal:.0f}",
                "protein": f"{ing.protein_g:.1f}",
                "fibre": f"{ing.fiber_g:.1f}",
                "fat": f"{ing.fat_g:.1f}",
                "carbs": f"{ing.carbs_g:.1f}",
                "matched to": match_text,
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    ingredient_kcal_sum = sum(ing.kcal for ing in ingredients)
    if meal_kcal > 0:
        delta = ingredient_kcal_sum - meal_kcal
        if abs(delta) > max(20.0, 0.1 * meal_kcal):
            st.caption(
                f"Ingredient sum: {ingredient_kcal_sum:.0f} kcal — "
                f"differs from stored per-serving total ({meal_kcal:.0f} kcal) "
                f"by {delta:+.0f}. Likely coverage gap below."
            )
    if flagged:
        st.warning("Suspicious entries: " + "; ".join(flagged))


def _meal_detail_popover(
    meal: MealEntry, day_user: DayPlanForProfile, ingredients: list[IngredientLine] | None
) -> None:
    with st.popover("Macros & detail", use_container_width=True):
        st.markdown(f"**{meal.title}**")
        if meal.rating is not None:
            st.caption(f"Recipe rating: {_stars(meal.rating)}")
        st.caption(_last_eaten_caption(meal.last_eaten))
        st.divider()
        rows = [
            ("Calories", f"{meal.kcal:.0f} kcal"),
            ("Protein", f"{meal.protein_g:.1f} g"),
            ("Fibre", f"{meal.fiber_g:.1f} g"),
            ("Fat", f"{meal.fat_g:.1f} g"),
            ("Carbs", f"{meal.carbs_g:.1f} g"),
        ]
        for label, value in rows:
            cols = st.columns([2, 3])
            cols[0].markdown(f"**{label}**")
            cols[1].markdown(value)
        if day_user.day_kcal > 0:
            share = 100.0 * meal.kcal / day_user.day_kcal
            st.caption(f"This meal contributes {share:.0f}% of today's calories.")
        if ingredients:
            st.divider()
            st.markdown("**Per-ingredient contribution**")
            _render_ingredient_table(ingredients, meal.kcal)


def _render_profile_day(
    day_user: DayPlanForProfile,
    meal_types: list[str],
    ingredients_by_recipe: dict[int, list[IngredientLine]],
) -> None:
    with st.container(border=True):
        st.markdown(f"**{day_user.display_name}**")
        shortfall_shown = False
        for meal_type in meal_types:
            is_snack = meal_type == "snack" or meal_type.startswith("snack_")
            entries = [m for m in day_user.meals if m.meal_type == meal_type]
            entry = entries[0] if entries else None
            label = _meal_label(meal_type)
            if entry is None or entry.title is None:
                if is_snack:
                    # Empty snack slots are hidden; show the self-source note once.
                    if day_user.gaps.any_shortfall and not shortfall_shown:
                        parts: list[str] = []
                        if day_user.gaps.kcal > 0:
                            parts.append(_format_gap("kcal", day_user.gaps.kcal, "kcal"))
                        if day_user.gaps.protein_g > 0:
                            parts.append(_format_gap("protein", day_user.gaps.protein_g, "g"))
                        if day_user.gaps.fiber_g > 0:
                            parts.append(_format_gap("fibre", day_user.gaps.fiber_g, "g"))
                        detail = " / ".join(parts) if parts else "no shortfall"
                        st.info(
                            f"**Snack**: source your own to top up — {detail} "
                            "(e.g. a high-protein yoghurt or smoothie)."
                        )
                        shortfall_shown = True
                else:
                    st.write(f"**{label}**: _(none)_")
                continue

            heading = f"**{label}**: {entry.title}"
            if entry.rating is not None:
                heading += f"  \n<span style='color:#b58900;font-size:0.85em'>{_stars(entry.rating)}</span>"
            heading += (
                f"  \n<span style='color:#666;font-size:0.85em'>{_format_macro(entry)}</span>"
            )
            if meal_type in ("lunch", "dinner"):
                last = (
                    f"Last scheduled: {_week_commencing(entry.last_eaten)}"
                    if entry.last_eaten is not None
                    else "Not previously scheduled"
                )
                heading += f"  \n<span style='color:#888;font-size:0.8em'>{last}</span>"
            elif entry.last_eaten is not None:
                heading += (
                    f"  \n<span style='color:#888;font-size:0.8em'>"
                    f"Last planned: {entry.last_eaten.isoformat()}</span>"
                )
            st.markdown(heading, unsafe_allow_html=True)
            recipe_ings = (
                ingredients_by_recipe.get(entry.recipe_id) if entry.recipe_id is not None else None
            )
            _meal_detail_popover(entry, day_user, recipe_ings)

        st.divider()
        # Compact single line so values are never cut off in side-by-side cards.
        st.markdown(
            "<div style='font-size:0.95em;line-height:1.7'>"
            f"<b>{day_user.day_kcal:.0f}</b> kcal &nbsp;·&nbsp; "
            f"<b>{day_user.day_protein_g:.0f}</b> g protein &nbsp;·&nbsp; "
            f"<b>{day_user.day_fiber_g:.0f}</b> g fibre &nbsp;·&nbsp; "
            f"<b>{day_user.day_fat_g:.0f}</b> g fat &nbsp;·&nbsp; "
            f"<b>{day_user.day_carbs_g:.0f}</b> g carbs"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_day(
    day: DayPlan,
    meal_types: list[str],
    ingredients_by_recipe: dict[int, list[IngredientLine]],
) -> None:
    st.subheader(f"Day {day.day}")
    if day.per_profile:
        # Daily Dozen is computed per household-day; show it once above the cards.
        st.markdown(
            "<span style='color:#666;font-size:0.85em'>Daily Dozen</span>", unsafe_allow_html=True
        )
        _render_dozen_strip(day.per_profile[0].daily_dozen)
    if len(day.per_profile) == 1:
        _render_profile_day(day.per_profile[0], meal_types, ingredients_by_recipe)
        return
    cols = st.columns(len(day.per_profile))
    for i, user_day in enumerate(day.per_profile):
        with cols[i]:
            _render_profile_day(user_day, meal_types, ingredients_by_recipe)


def _render_plan(view: PlanView, meal_types: list[str]) -> None:
    header = st.columns(4)
    header[0].metric("Plan run", view.plan_run_id)
    header[1].metric("Relaxation", view.relaxation_level)
    header[2].metric("Solver", view.solver_status)
    header[3].metric("Slack total", f"{view.slack_total:.1f}")
    st.caption(f"Run time: {view.run_time} · correlation_id: {view.correlation_id}")
    for day in view.days:
        _render_day(day, meal_types, view.recipe_ingredients)


def _render_dashboard(view: PlanView, settings: Settings) -> None:
    st.markdown("### Daily Dozen alignment")
    met, partial, missed, gaps = dozen_summary_counts(view)
    cols = st.columns(3)
    cols[0].metric("Groups fully met (every day)", met)
    cols[1].metric("Partial", partial)
    cols[2].metric("Gaps (zero portions)", missed)
    if gaps:
        st.warning("**Completely missing this plan:** " + ", ".join(gaps))
    st.caption(
        "Greger's Daily Dozen is a per-day prescription — bars below show "
        "how many days each group hit its daily target."
    )
    st.plotly_chart(daily_dozen_weekly_chart(view), use_container_width=True)
    with st.expander("Daily heatmap (per-day detail)", expanded=False):
        st.plotly_chart(daily_dozen_heatmap(view), use_container_width=True)

    st.markdown("### Daily macros vs targets")
    st.plotly_chart(
        daily_macros_chart(view, settings.optimizer, settings.household.profiles),
        use_container_width=True,
    )
    st.markdown("### Calories per meal across the week")
    st.plotly_chart(day_meal_stack_chart(view), use_container_width=True)
    st.markdown("### Recipe frequency by user")
    st.plotly_chart(recipe_frequency_chart(view), use_container_width=True)


def _run_pipeline(settings: Settings) -> int:
    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    engine = get_engine()
    if not wait_for_db(engine):
        raise RuntimeError("database is not reachable")
    result = optimize_plan(settings, engine=engine)
    return write_plan(settings, result, engine=engine)


NO_FIXED_BREAKFAST = "(optimiser chooses)"


@st.cache_data(show_spinner=False)
def _breakfast_titles() -> list[str]:
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT r.title
                FROM meal_planning.recipe r
                JOIN meal_planning.recipe_meal_type mt ON mt.recipe_id = r.recipe_id
                WHERE mt.meal_type = 'breakfast' AND r.is_plant_based = TRUE
                ORDER BY r.title
                """
            )
        ).fetchall()
    return [str(row[0]) for row in rows]


def _profile_widgets(
    label: str, key_prefix: str, name_default: str, breakfast_options: list[str]
) -> ProfileTargets:
    name = st.text_input(f"{label} name", value=name_default, key=f"{key_prefix}_name")
    cal_min, cal_max = st.slider(
        f"{label} calories range (kcal)",
        min_value=1000,
        max_value=4000,
        value=(1800, 2400),
        step=50,
        key=f"{key_prefix}_kcal",
    )
    protein_min = st.number_input(
        f"{label} protein min (g/day)",
        min_value=0,
        max_value=300,
        value=60,
        step=5,
        key=f"{key_prefix}_protein",
    )
    fiber_min = st.number_input(
        f"{label} fibre min (g/day)",
        min_value=0,
        max_value=120,
        value=30,
        step=1,
        key=f"{key_prefix}_fibre",
    )
    fixed_breakfast = st.selectbox(
        f"{label} fixed breakfast (same every day)",
        [NO_FIXED_BREAKFAST, *breakfast_options],
        key=f"{key_prefix}_fixed_breakfast",
        help="Pin one recipe to this person's breakfast every day; "
        "the optimiser plans the rest of the day around it.",
    )
    fixed_meals: dict[str, str] = {}
    if fixed_breakfast and fixed_breakfast != NO_FIXED_BREAKFAST:
        fixed_meals["breakfast"] = fixed_breakfast
    return ProfileTargets(
        name=name,
        display_name=name,
        calories_daily_min=int(cal_min),
        calories_daily_max=int(cal_max),
        protein_daily_min=int(protein_min),
        fiber_daily_min=int(fiber_min),
        fixed_meals=fixed_meals,
    )


def render() -> None:
    configure_logging("INFO", "json")
    st.set_page_config(page_title="Meal Planner", layout="wide")
    st.title("Plant-based household meal planner")

    config_path_input = st.sidebar.text_input(
        "Config path", value=str(Path("config/pipeline.yaml"))
    )
    config_path = Path(config_path_input)
    if not config_path.exists():
        st.sidebar.error(f"Config not found: {config_path}")
        return
    base_settings = _bootstrap_settings(config_path)

    st.sidebar.header("Household")
    use_two = st.sidebar.checkbox("Two users", value=True)
    shared = st.sidebar.multiselect(
        "Shared meals (same dish for both users each day)",
        list(ALL_MEAL_TYPES),
        default=list(DEFAULT_SHARED),
        disabled=not use_two,
    )

    breakfast_options = _breakfast_titles()
    if use_two:
        with st.sidebar.expander("User A targets", expanded=True):
            profile_a = _profile_widgets("User A", "a", "user_a", breakfast_options)
        with st.sidebar.expander("User B targets", expanded=True):
            profile_b = _profile_widgets("User B", "b", "user_b", breakfast_options)
        profiles = [profile_a, profile_b]
        if profile_a.name == profile_b.name:
            st.sidebar.error("Profile names must be unique")
            return
    else:
        with st.sidebar.expander("User targets", expanded=True):
            profiles = [_profile_widgets("User", "single", "default", breakfast_options)]

    st.sidebar.header("Variety & ratings")
    spacing_weight = st.sidebar.slider(
        "Spacing penalty weight",
        min_value=0.0,
        max_value=10.0,
        value=float(base_settings.optimizer.spacing_weight),
        step=0.5,
        help="Higher = stronger penalty for repeating a recipe on consecutive days.",
    )
    rating_weight = st.sidebar.slider(
        "Rating weight",
        min_value=0.0,
        max_value=10.0,
        value=float(base_settings.optimizer.rating_weight),
        step=0.5,
        help="Higher = solver prefers higher-rated recipes more strongly.",
    )
    min_rating = st.sidebar.slider(
        "Minimum recipe rating",
        min_value=0.0,
        max_value=5.0,
        value=float(base_settings.optimizer.min_rating),
        step=0.5,
        help="Recipes below this rating are filtered out before optimisation.",
    )
    max_repeats = st.sidebar.slider(
        "Max times any recipe can repeat in a week",
        min_value=1,
        max_value=4,
        value=int(base_settings.optimizer.max_recipe_repeats),
    )
    horizon = st.sidebar.slider(
        "Planning horizon (days)",
        min_value=3,
        max_value=14,
        value=int(base_settings.optimizer.planning_horizon_days),
    )
    max_snacks = st.sidebar.slider(
        "Max snacks per day",
        min_value=1,
        max_value=5,
        value=int(base_settings.optimizer.max_snacks_per_day),
        help="Up to this many snacks per person per day (filled only when they "
        "help hit targets). At most one snack may be a smoothie.",
    )

    settings = base_settings.model_copy(
        update={
            "household": HouseholdSettings(
                profiles=profiles,
                shared_meal_types=shared if use_two else [],
            ),
            "optimizer": base_settings.optimizer.model_copy(
                update={
                    "spacing_weight": spacing_weight,
                    "rating_weight": rating_weight,
                    "min_rating": min_rating,
                    "max_recipe_repeats": max_repeats,
                    "planning_horizon_days": horizon,
                    "max_snacks_per_day": max_snacks,
                    "snack_optional": "snack" in base_settings.meal_types,
                    "calories_daily_min": None,
                    "calories_daily_max": None,
                    "fiber_daily_min": None,
                    "protein_daily_min": None,
                    "protein_daily_max": None,
                }
            ),
        }
    )

    if st.sidebar.button("Generate plan", type="primary"):
        with st.spinner("Optimising…"):
            try:
                plan_run_id = _run_pipeline(settings)
                st.session_state["last_plan_run_id"] = plan_run_id
                st.success(f"Plan generated · plan_run_id={plan_run_id}")
            except Exception as exc:
                st.error(f"Plan failed: {exc}")

    selected_id = cast(int | None, st.session_state.get("last_plan_run_id"))
    dozen_targets = dict(settings.daily_dozen_targets)
    if selected_id is None:
        view = load_latest_plan_view(
            settings.optimizer, settings.household, daily_dozen_targets=dozen_targets
        )
    else:
        view = load_plan_view(
            selected_id, settings.optimizer, settings.household, daily_dozen_targets=dozen_targets
        )

    if view is None:
        st.info("No plan run yet — set parameters in the sidebar and click **Generate plan**.")
        return

    plan_tab, dashboard_tab = st.tabs(["Plan view", "Dashboard"])
    with plan_tab:
        _render_plan(view, _ordered_meal_types(view))
    with dashboard_tab:
        _render_dashboard(view, settings)


def main() -> None:
    render()


if __name__ == "__main__":
    main()
