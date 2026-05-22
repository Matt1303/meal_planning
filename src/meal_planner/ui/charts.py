from __future__ import annotations

from collections import Counter

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from meal_planner.config import OptimizerSettings, ProfileTargets
from meal_planner.ui.data import PlanView

MEAL_COLORS = {
    "breakfast": "#f6a96b",
    "lunch": "#6ec3a1",
    "dinner": "#5a8fd6",
    "snack": "#c08fd6",
}


def _targets_for_profile(
    profile_name: str, opt: OptimizerSettings, profiles: list[ProfileTargets]
) -> ProfileTargets:
    for profile in profiles:
        if profile.name == profile_name:
            return profile
    return ProfileTargets(
        name=profile_name,
        calories_daily_min=opt.calories_daily_min,
        calories_daily_max=opt.calories_daily_max,
        fiber_daily_min=opt.fiber_daily_min,
        protein_daily_min=opt.protein_daily_min,
        protein_daily_max=opt.protein_daily_max,
    )


def daily_macros_chart(
    view: PlanView, opt: OptimizerSettings, profiles: list[ProfileTargets]
) -> go.Figure:
    profile_names: list[str] = []
    for day in view.days:
        for entry in day.per_profile:
            if entry.profile_name not in profile_names:
                profile_names.append(entry.profile_name)

    fig = make_subplots(
        rows=len(profile_names),
        cols=3,
        shared_xaxes=True,
        subplot_titles=[
            f"{name} · {macro}"
            for name in profile_names
            for macro in ("calories", "protein", "fibre")
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.06,
    )

    days = [d.day for d in view.days]

    for row_idx, profile_name in enumerate(profile_names, start=1):
        targets = _targets_for_profile(profile_name, opt, profiles)
        kcal_vals: list[float] = []
        protein_vals: list[float] = []
        fibre_vals: list[float] = []
        for day in view.days:
            for entry in day.per_profile:
                if entry.profile_name == profile_name:
                    kcal_vals.append(entry.day_kcal)
                    protein_vals.append(entry.day_protein_g)
                    fibre_vals.append(entry.day_fiber_g)
                    break

        for col_idx, (vals, color, label, t_min, t_max) in enumerate(
            (
                (
                    kcal_vals,
                    "#5a8fd6",
                    "kcal",
                    targets.calories_daily_min,
                    targets.calories_daily_max,
                ),
                (
                    protein_vals,
                    "#6ec3a1",
                    "g",
                    targets.protein_daily_min,
                    targets.protein_daily_max,
                ),
                (fibre_vals, "#f6a96b", "g", targets.fiber_daily_min, None),
            ),
            start=1,
        ):
            fig.add_trace(
                go.Bar(
                    x=days,
                    y=vals,
                    marker_color=color,
                    name=label,
                    showlegend=False,
                    hovertemplate="Day %{x}: %{y:.0f} " + label + "<extra></extra>",
                ),
                row=row_idx,
                col=col_idx,
            )
            if t_min is not None:
                fig.add_hline(
                    y=float(t_min),
                    line_dash="dash",
                    line_color="#999",
                    annotation_text=f"min {t_min}",
                    annotation_position="top left",
                    row=row_idx,
                    col=col_idx,
                )
            if t_max is not None:
                fig.add_hline(
                    y=float(t_max),
                    line_dash="dot",
                    line_color="#999",
                    annotation_text=f"max {t_max}",
                    annotation_position="bottom left",
                    row=row_idx,
                    col=col_idx,
                )

    fig.update_layout(
        height=260 * len(profile_names),
        margin={"t": 50, "b": 40, "l": 40, "r": 20},
        showlegend=False,
    )
    fig.update_xaxes(title_text="Day", tickmode="array", tickvals=days)
    return fig


def day_meal_stack_chart(view: PlanView) -> go.Figure:
    profile_names: list[str] = []
    for day in view.days:
        for entry in day.per_profile:
            if entry.profile_name not in profile_names:
                profile_names.append(entry.profile_name)

    fig = make_subplots(
        rows=1,
        cols=len(profile_names),
        shared_yaxes=True,
        subplot_titles=profile_names,
        horizontal_spacing=0.06,
    )

    days = [d.day for d in view.days]
    meal_types = sorted(
        {m.meal_type for day in view.days for p in day.per_profile for m in p.meals}
    )

    for col_idx, profile_name in enumerate(profile_names, start=1):
        for meal_type in meal_types:
            kcals: list[float] = []
            titles: list[str] = []
            for day in view.days:
                day_total = 0.0
                day_titles: list[str] = []
                for profile_entry in day.per_profile:
                    if profile_entry.profile_name != profile_name:
                        continue
                    for meal in profile_entry.meals:
                        if meal.meal_type == meal_type:
                            day_total += meal.kcal
                            if meal.title:
                                day_titles.append(meal.title)
                kcals.append(day_total)
                titles.append(", ".join(day_titles) if day_titles else "—")
            fig.add_trace(
                go.Bar(
                    x=days,
                    y=kcals,
                    name=meal_type.title(),
                    marker_color=MEAL_COLORS.get(meal_type, "#888"),
                    legendgroup=meal_type,
                    showlegend=(col_idx == 1),
                    customdata=titles,
                    hovertemplate=(
                        "Day %{x} · " + meal_type + "<br>"
                        "%{customdata}<br>"
                        "%{y:.0f} kcal<extra></extra>"
                    ),
                ),
                row=1,
                col=col_idx,
            )

    fig.update_layout(
        barmode="stack",
        height=420,
        margin={"t": 50, "b": 40, "l": 40, "r": 20},
        legend={"orientation": "h", "y": -0.15},
    )
    fig.update_xaxes(title_text="Day", tickmode="array", tickvals=days)
    fig.update_yaxes(title_text="kcal", col=1)
    return fig


def recipe_frequency_chart(view: PlanView) -> go.Figure:
    counts: dict[str, Counter[str]] = {}
    for day in view.days:
        for profile_entry in day.per_profile:
            counter = counts.setdefault(profile_entry.profile_name, Counter())
            for meal in profile_entry.meals:
                if meal.title is not None:
                    counter[meal.title] += 1

    all_titles: list[str] = []
    for counter in counts.values():
        for title in counter:
            if title not in all_titles:
                all_titles.append(title)

    if not all_titles:
        fig = go.Figure()
        fig.update_layout(annotations=[{"text": "No recipes selected yet.", "showarrow": False}])
        return fig

    totals: Counter[str] = Counter()
    for counter in counts.values():
        totals.update(counter)
    sorted_titles = [t for t, _ in totals.most_common()]

    fig = go.Figure()
    for profile_name, counter in counts.items():
        fig.add_trace(
            go.Bar(
                x=[counter.get(t, 0) for t in sorted_titles],
                y=sorted_titles,
                orientation="h",
                name=profile_name,
                hovertemplate="%{y}<br>%{x} time(s)<extra>" + profile_name + "</extra>",
            )
        )

    fig.update_layout(
        barmode="group",
        height=max(320, 28 * len(sorted_titles)),
        margin={"t": 30, "b": 40, "l": 220, "r": 20},
        xaxis_title="Times served this plan",
        yaxis={"autorange": "reversed"},
        legend={"orientation": "h", "y": -0.1},
    )
    return fig


def daily_dozen_heatmap(view: PlanView) -> go.Figure:
    groups: list[str] = []
    days = [d.day for d in view.days]
    for day in view.days:
        for entry in day.per_profile:
            for group in entry.daily_dozen:
                if group not in groups:
                    groups.append(group)
            break
    groups.sort()

    if not groups:
        fig = go.Figure()
        fig.update_layout(annotations=[{"text": "No daily-dozen data.", "showarrow": False}])
        return fig

    z: list[list[float]] = []
    custom: list[list[str]] = []
    for group in groups:
        row: list[float] = []
        info: list[str] = []
        for day in view.days:
            count = 0
            target = 0
            portions = 0.0
            for entry in day.per_profile:
                triple = entry.daily_dozen.get(group)
                if triple is None:
                    continue
                count, target, portions = triple
                break
            ratio = (count / target) if target > 0 else 0.0
            row.append(ratio)
            info.append(f"{count} / {target} · {portions:.1f} portions")
        z.append(row)
        custom.append(info)

    fig = go.Figure(
        data=go.Heatmap(
            x=days,
            y=groups,
            z=z,
            customdata=custom,
            colorscale="YlGn",
            zmin=0,
            zmax=1.5,
            colorbar={"title": "count ÷ target"},
            hovertemplate="%{y} · day %{x}<br>%{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        height=max(340, 24 * len(groups)),
        margin={"t": 30, "b": 40, "l": 140, "r": 20},
        xaxis_title="Day",
        xaxis={"tickmode": "array", "tickvals": days},
    )
    return fig
