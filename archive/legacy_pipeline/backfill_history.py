from sqlalchemy import text
from .db import get_engine


def backfill_history():
    engine = get_engine()
    with engine.begin() as conn:
        titles = conn.execute(text("SELECT recipe_id, title FROM meal_planning.recipe")).fetchall()
        title_map = {t[1]: t[0] for t in titles}

        columns = conn.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'meal_planning' AND table_name = 'weekly_meal_plan'
                """
            )
        ).fetchall()
        cols = {c[0] for c in columns}
        meal_cols = []
        for name in ["breakfast", "lunch", "dinner", "snack", "breakfasts", "lunches", "snacks"]:
            if name in cols:
                meal_cols.append(name)

        if not meal_cols:
            return 0

        rows = conn.execute(text("SELECT run_time, " + ", ".join(meal_cols) + " FROM meal_planning.weekly_meal_plan")).fetchall()
        inserted = 0
        for row in rows:
            run_time = row[0]
            for idx, meal_col in enumerate(meal_cols):
                title = row[idx + 1]
                if not title:
                    continue
                recipe_id = title_map.get(title)
                if not recipe_id:
                    continue
                meal_type = meal_col.rstrip("s")
                conn.execute(
                    text(
                        "INSERT INTO meal_planning.meal_history (recipe_id, meal_type, planned_for) VALUES (:recipe_id, :meal_type, :planned_for) ON CONFLICT DO NOTHING"
                    ),
                    {"recipe_id": recipe_id, "meal_type": meal_type, "planned_for": run_time.date()},
                )
                inserted += 1
        return inserted
