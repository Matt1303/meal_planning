from __future__ import annotations

from pathlib import Path
from typing import cast

import streamlit as st

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
from meal_planner.correlation import new_correlation_id, set_correlation_id
from meal_planner.db import get_engine, wait_for_db
from meal_planner.logging import configure as configure_logging
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.ui.data import (
    DayPlan,
    DayPlanForProfile,
    MealEntry,
    PlanView,
    load_latest_plan_view,
    load_plan_view,
)

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


def _render_profile_day(day_user: DayPlanForProfile, meal_types: list[str]) -> None:
    with st.container(border=True):
        st.markdown(f"**{day_user.display_name}**")
        for meal_type in meal_types:
            entries = [m for m in day_user.meals if m.meal_type == meal_type]
            entry = entries[0] if entries else None
            if entry is None or entry.title is None:
                if meal_type == "snack" and day_user.gaps.any_shortfall:
                    parts: list[str] = []
                    if day_user.gaps.kcal > 0:
                        parts.append(_format_gap("kcal", day_user.gaps.kcal, "kcal"))
                    if day_user.gaps.protein_g > 0:
                        parts.append(_format_gap("protein", day_user.gaps.protein_g, "g"))
                    if day_user.gaps.fiber_g > 0:
                        parts.append(_format_gap("fibre", day_user.gaps.fiber_g, "g"))
                    detail = " / ".join(parts) if parts else "no shortfall"
                    st.info(
                        f"**{meal_type.title()}**: source your own to top up — {detail} "
                        "(e.g. a high-protein yoghurt or smoothie)."
                    )
                else:
                    st.write(f"**{meal_type.title()}**: _(none)_")
            else:
                st.markdown(
                    f"**{meal_type.title()}**: {entry.title}  \n"
                    f"<span style='color:#666;font-size:0.85em'>{_format_macro(entry)}</span>",
                    unsafe_allow_html=True,
                )

        st.divider()
        cols = st.columns(5)
        cols[0].metric("Calories", f"{day_user.day_kcal:.0f} kcal")
        cols[1].metric("Protein", f"{day_user.day_protein_g:.0f} g")
        cols[2].metric("Fibre", f"{day_user.day_fiber_g:.0f} g")
        cols[3].metric("Fat", f"{day_user.day_fat_g:.0f} g")
        cols[4].metric("Carbs", f"{day_user.day_carbs_g:.0f} g")


def _render_day(day: DayPlan, meal_types: list[str]) -> None:
    st.subheader(f"Day {day.day}")
    if len(day.per_profile) == 1:
        _render_profile_day(day.per_profile[0], meal_types)
        return
    cols = st.columns(len(day.per_profile))
    for i, user_day in enumerate(day.per_profile):
        with cols[i]:
            _render_profile_day(user_day, meal_types)


def _render_plan(view: PlanView, meal_types: list[str]) -> None:
    header = st.columns(4)
    header[0].metric("Plan run", view.plan_run_id)
    header[1].metric("Relaxation", view.relaxation_level)
    header[2].metric("Solver", view.solver_status)
    header[3].metric("Slack total", f"{view.slack_total:.1f}")
    st.caption(f"Run time: {view.run_time} · correlation_id: {view.correlation_id}")
    for day in view.days:
        _render_day(day, meal_types)


def _run_pipeline(settings: Settings) -> int:
    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    engine = get_engine()
    if not wait_for_db(engine):
        raise RuntimeError("database is not reachable")
    result = optimize_plan(settings, engine=engine)
    return write_plan(settings, result, engine=engine)


def _profile_widgets(label: str, key_prefix: str, name_default: str) -> ProfileTargets:
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
    return ProfileTargets(
        name=name,
        display_name=name,
        calories_daily_min=int(cal_min),
        calories_daily_max=int(cal_max),
        protein_daily_min=int(protein_min),
        fiber_daily_min=int(fiber_min),
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

    if use_two:
        with st.sidebar.expander("User A targets", expanded=True):
            profile_a = _profile_widgets("User A", "a", "user_a")
        with st.sidebar.expander("User B targets", expanded=True):
            profile_b = _profile_widgets("User B", "b", "user_b")
        profiles = [profile_a, profile_b]
        if profile_a.name == profile_b.name:
            st.sidebar.error("Profile names must be unique")
            return
    else:
        with st.sidebar.expander("User targets", expanded=True):
            profiles = [_profile_widgets("User", "single", "default")]

    st.sidebar.header("Variety")
    spacing_weight = st.sidebar.slider(
        "Spacing penalty weight",
        min_value=0.0,
        max_value=10.0,
        value=float(base_settings.optimizer.spacing_weight),
        step=0.5,
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

    settings = base_settings.model_copy(
        update={
            "household": HouseholdSettings(
                profiles=profiles,
                shared_meal_types=shared if use_two else [],
            ),
            "optimizer": base_settings.optimizer.model_copy(
                update={
                    "spacing_weight": spacing_weight,
                    "max_recipe_repeats": max_repeats,
                    "planning_horizon_days": horizon,
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
    if selected_id is None:
        view = load_latest_plan_view(settings.optimizer, settings.household)
    else:
        view = load_plan_view(selected_id, settings.optimizer, settings.household)

    if view is None:
        st.info("No plan run yet — set parameters in the sidebar and click **Generate plan**.")
        return

    _render_plan(view, list(base_settings.meal_types))


def main() -> None:
    render()


if __name__ == "__main__":
    main()
