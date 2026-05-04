from __future__ import annotations

from pathlib import Path

import pandas as pd

from meal_planner.report.data import ReportData


def save_kcal_fiber(data: ReportData, dest: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax1.plot(data.plan_day["day"], data.plan_day["kcal"], marker="o", color="#1f77b4")
    ax1.set_xlabel("Day")
    ax1.set_ylabel("Calories (kcal)", color="#1f77b4")
    ax2 = ax1.twinx()
    ax2.plot(data.plan_day["day"], data.plan_day["fiber_g"], marker="s", color="#2ca02c")
    ax2.set_ylabel("Fiber (g)", color="#2ca02c")
    ax1.set_title("Daily Calories and Fiber")
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    return dest


def save_group_heatmap(data: ReportData, dest: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    pivot = data.plan_group.pivot(index="food_group", columns="day", values="daily_count").fillna(0)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    sns.heatmap(pivot, annot=True, fmt=".0f", cmap="YlGnBu", ax=ax)
    ax.set_title("Daily Dozen Group Coverage (Counts)")
    ax.set_xlabel("Day")
    ax.set_ylabel("Food Group")
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    return dest


def save_top_ingredients(ingredients: pd.Series, dest: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if ingredients.empty:
        return None
    counts = ingredients.value_counts().head(15)
    fig, ax = plt.subplots(figsize=(7, 4))
    counts.sort_values().plot(kind="barh", ax=ax, color="#ff7f0e")
    ax.set_title("Top Ingredients (by occurrences)")
    ax.set_xlabel("Count")
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=150)
    plt.close(fig)
    return dest
