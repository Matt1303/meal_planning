from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Engine, text

from meal_planner.config import OptimizerSettings
from meal_planner.db import get_engine


@dataclass(frozen=True)
class MealEntry:
    meal_type: str
    title: str | None
    recipe_id: int | None
    kcal: float
    fiber_g: float
    protein_g: float
    fat_g: float
    carbs_g: float


@dataclass(frozen=True)
class NutritionGaps:
    kcal: float = 0.0
    fiber_g: float = 0.0
    protein_g: float = 0.0

    @property
    def any_shortfall(self) -> bool:
        return self.kcal > 0 or self.fiber_g > 0 or self.protein_g > 0


@dataclass(frozen=True)
class DayPlan:
    day: int
    meals: list[MealEntry]
    day_kcal: float
    day_fiber_g: float
    day_protein_g: float
    day_fat_g: float
    day_carbs_g: float
    daily_dozen: dict[str, tuple[int, int, float]]
    gaps: NutritionGaps = field(default_factory=NutritionGaps)

    def empty_snack_slots(self) -> list[MealEntry]:
        return [m for m in self.meals if m.meal_type == "snack" and m.title is None]


@dataclass(frozen=True)
class PlanView:
    plan_run_id: int
    run_time: str
    solver_status: str
    relaxation_level: int
    slack_total: float
    correlation_id: str | None
    days: list[DayPlan]


def compute_gaps(day: DayPlan, opt: OptimizerSettings) -> NutritionGaps:
    kcal_gap = 0.0
    fiber_gap = 0.0
    protein_gap = 0.0
    if opt.calories_daily_min is not None and day.day_kcal < float(opt.calories_daily_min):
        kcal_gap = float(opt.calories_daily_min) - day.day_kcal
    if opt.fiber_daily_min is not None and day.day_fiber_g < float(opt.fiber_daily_min):
        fiber_gap = float(opt.fiber_daily_min) - day.day_fiber_g
    if opt.protein_daily_min is not None and day.day_protein_g < float(opt.protein_daily_min):
        protein_gap = float(opt.protein_daily_min) - day.day_protein_g
    return NutritionGaps(kcal=kcal_gap, fiber_g=fiber_gap, protein_g=protein_gap)


def _f(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def load_plan_view(
    plan_run_id: int, opt: OptimizerSettings, *, engine: Engine | None = None
) -> PlanView | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        run_row = conn.execute(
            text(
                """
                SELECT plan_run_id, run_time, COALESCE(solver_status, ''),
                       COALESCE(relaxation_level, 0), COALESCE(slack_total, 0), correlation_id
                FROM meal_planning.plan_run WHERE plan_run_id = :pr
                """
            ),
            {"pr": plan_run_id},
        ).fetchone()
        if run_row is None:
            return None

        meal_rows = conn.execute(
            text(
                """
                SELECT pm.day, pm.meal_type, pm.recipe_id, r.title,
                       rn.per_serving_kcal, rn.per_serving_fiber_g,
                       rn.per_serving_protein_g, rn.per_serving_fat_g, rn.per_serving_carbs_g
                FROM meal_planning.plan_meal pm
                LEFT JOIN meal_planning.recipe r ON r.recipe_id = pm.recipe_id
                LEFT JOIN meal_planning.recipe_nutrition rn ON rn.recipe_id = pm.recipe_id
                WHERE pm.plan_run_id = :pr
                ORDER BY pm.day, pm.meal_type
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

        day_rows = conn.execute(
            text(
                """
                SELECT day, kcal, fiber_g, protein_g, fat_g, carbs_g
                FROM meal_planning.plan_day WHERE plan_run_id = :pr
                ORDER BY day
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

        group_rows = conn.execute(
            text(
                """
                SELECT day, food_group, daily_count, daily_portions
                FROM meal_planning.plan_day_group WHERE plan_run_id = :pr
                ORDER BY day, food_group
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

    meals_by_day: dict[int, list[MealEntry]] = {}
    for row in meal_rows:
        meals_by_day.setdefault(int(row[0]), []).append(
            MealEntry(
                meal_type=str(row[1]),
                title=row[3],
                recipe_id=int(row[2]) if row[2] is not None else None,
                kcal=_f(row[4]),
                fiber_g=_f(row[5]),
                protein_g=_f(row[6]),
                fat_g=_f(row[7]),
                carbs_g=_f(row[8]),
            )
        )

    day_totals: dict[int, tuple[float, float, float, float, float]] = {
        int(row[0]): (_f(row[1]), _f(row[2]), _f(row[3]), _f(row[4]), _f(row[5]))
        for row in day_rows
    }

    groups_by_day: dict[int, dict[str, tuple[int, int, float]]] = {}
    targets = {}
    for group_name, target in opt.model_dump(mode="json").get("daily_dozen_targets", {}).items():
        targets[group_name] = int(target)

    for row in group_rows:
        day_int = int(row[0])
        group = str(row[1])
        count = int(row[2] or 0)
        portions = _f(row[3])
        target = targets.get(group, 0)
        groups_by_day.setdefault(day_int, {})[group] = (count, target, portions)

    days: list[DayPlan] = []
    for day_int in sorted(meals_by_day.keys()):
        totals = day_totals.get(day_int, (0.0, 0.0, 0.0, 0.0, 0.0))
        day_plan = DayPlan(
            day=day_int,
            meals=meals_by_day[day_int],
            day_kcal=totals[0],
            day_fiber_g=totals[1],
            day_protein_g=totals[2],
            day_fat_g=totals[3],
            day_carbs_g=totals[4],
            daily_dozen=groups_by_day.get(day_int, {}),
        )
        gaps = compute_gaps(day_plan, opt)
        days.append(
            DayPlan(
                day=day_plan.day,
                meals=day_plan.meals,
                day_kcal=day_plan.day_kcal,
                day_fiber_g=day_plan.day_fiber_g,
                day_protein_g=day_plan.day_protein_g,
                day_fat_g=day_plan.day_fat_g,
                day_carbs_g=day_plan.day_carbs_g,
                daily_dozen=day_plan.daily_dozen,
                gaps=gaps,
            )
        )

    return PlanView(
        plan_run_id=int(run_row[0]),
        run_time=str(run_row[1]),
        solver_status=str(run_row[2]),
        relaxation_level=int(run_row[3]),
        slack_total=float(run_row[4] or 0),
        correlation_id=run_row[5],
        days=days,
    )


def load_latest_plan_view(
    opt: OptimizerSettings, *, engine: Engine | None = None
) -> PlanView | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1")
        ).fetchone()
    if row is None:
        return None
    return load_plan_view(int(row[0]), opt, engine=eng)
