from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import streamlit as st

from meal_planner.config import Settings
from meal_planner.correlation import new_correlation_id, set_correlation_id
from meal_planner.db import get_engine, wait_for_db
from meal_planner.logging import configure as configure_logging
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.ui.data import DayPlan, MealEntry, PlanView, load_latest_plan_view

ALL_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


def _bootstrap_settings(config_path: Path) -> Settings:
    return Settings.load(config_path)


def _apply_overrides(base: Settings, overrides: dict[str, Any]) -> Settings:
    new_optimizer = base.optimizer.model_copy(update=overrides)
    return base.model_copy(update={"optimizer": new_optimizer})


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


def _render_day(day: DayPlan, meal_types: list[str]) -> None:
    with st.container(border=True):
        st.subheader(f"Day {day.day}")
        for meal_type in meal_types:
            entry = next((m for m in day.meals if m.meal_type == meal_type), None)
            if entry is None or entry.title is None:
                if meal_type == "snack" and day.gaps.any_shortfall:
                    parts: list[str] = []
                    if day.gaps.kcal > 0:
                        parts.append(_format_gap("kcal", day.gaps.kcal, "kcal"))
                    if day.gaps.protein_g > 0:
                        parts.append(_format_gap("protein", day.gaps.protein_g, "g"))
                    if day.gaps.fiber_g > 0:
                        parts.append(_format_gap("fibre", day.gaps.fiber_g, "g"))
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
        cols[0].metric("Calories", f"{day.day_kcal:.0f} kcal")
        cols[1].metric("Protein", f"{day.day_protein_g:.0f} g")
        cols[2].metric("Fibre", f"{day.day_fiber_g:.0f} g")
        cols[3].metric("Fat", f"{day.day_fat_g:.0f} g")
        cols[4].metric("Carbs", f"{day.day_carbs_g:.0f} g")

        if day.daily_dozen:
            st.caption("Daily Dozen group counts (ingredient ticks / target)")
            cols = st.columns(min(5, len(day.daily_dozen)) or 1)
            for i, (group, (count, target, _)) in enumerate(sorted(day.daily_dozen.items())):
                col = cols[i % len(cols)]
                ok = "✓" if count >= target else "·"
                col.write(f"{ok} {group}: {count}/{target}")


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


def render() -> None:
    configure_logging("INFO", "json")
    st.set_page_config(page_title="Meal Planner", layout="wide")
    st.title("Plant-based meal planner")

    config_path_input = st.sidebar.text_input(
        "Config path", value=str(Path("config/pipeline.yaml"))
    )
    config_path = Path(config_path_input)
    if not config_path.exists():
        st.sidebar.error(f"Config not found: {config_path}")
        return
    base_settings = _bootstrap_settings(config_path)

    st.sidebar.header("Meals per day")
    meal_types = st.sidebar.multiselect(
        "Slots to fill",
        list(ALL_MEAL_TYPES),
        default=list(base_settings.meal_types),
    )

    st.sidebar.header("Daily nutrition targets")
    kcal_min, kcal_max = st.sidebar.slider(
        "Calories range (kcal)",
        min_value=1000,
        max_value=4000,
        value=(
            base_settings.optimizer.calories_daily_min or 1800,
            base_settings.optimizer.calories_daily_max or 2400,
        ),
        step=50,
    )
    enforce_kcal = st.sidebar.checkbox("Enforce calorie range", value=True)

    protein_target = st.sidebar.number_input(
        "Protein target (g/day, min)",
        min_value=0,
        max_value=300,
        value=base_settings.optimizer.protein_daily_min or 60,
        step=5,
    )
    enforce_protein = st.sidebar.checkbox(
        "Enforce protein min", value=base_settings.optimizer.protein_daily_min is not None
    )
    fiber_target = st.sidebar.number_input(
        "Fibre target (g/day, min)",
        min_value=0,
        max_value=120,
        value=base_settings.optimizer.fiber_daily_min or 30,
        step=1,
    )
    enforce_fiber = st.sidebar.checkbox("Enforce fibre min", value=True)

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

    overrides: dict[str, Any] = {
        "calories_daily_min": kcal_min if enforce_kcal else None,
        "calories_daily_max": kcal_max if enforce_kcal else None,
        "fiber_daily_min": int(fiber_target) if enforce_fiber else None,
        "protein_daily_min": int(protein_target) if enforce_protein else None,
        "spacing_weight": spacing_weight,
        "max_recipe_repeats": max_repeats,
        "planning_horizon_days": horizon,
        "snack_optional": "snack" in meal_types,
    }
    settings = base_settings.model_copy(update={"meal_types": meal_types or list(ALL_MEAL_TYPES)})
    settings = _apply_overrides(settings, overrides)

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
        view = load_latest_plan_view(settings.optimizer)
    else:
        from meal_planner.ui.data import load_plan_view

        view = load_plan_view(selected_id, settings.optimizer)

    if view is None:
        st.info("No plan run yet — set parameters in the sidebar and click **Generate plan**.")
        return

    _render_plan(view, meal_types or list(ALL_MEAL_TYPES))


def main() -> None:
    render()


if __name__ == "__main__":
    main()
