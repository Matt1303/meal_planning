from __future__ import annotations

from typing import Any


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException

    from meal_planner.db import get_engine, wait_for_db
    from meal_planner.report import ReportFormats, generate_report
    from meal_planner.report.data import load_report_data
    from meal_planner.report.html import render_html

    app = FastAPI(title="meal-planner")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        engine = get_engine()
        ok = wait_for_db(engine, retries=1, delay=0.5)
        if not ok:
            raise HTTPException(status_code=503, detail="db unreachable")
        return {"status": "ok"}

    @app.get("/livez")
    def livez() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/plan/latest", response_model=None)
    def latest_plan() -> Any:
        from fastapi.responses import HTMLResponse
        from sqlalchemy import text

        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1"
                )
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no plan run yet")
        plan_run_id = int(row[0])
        data = load_report_data(plan_run_id, engine=engine)
        outputs = generate_report(
            plan_run_id,
            engine=engine,
            formats=ReportFormats(md=False, html=False, docx=False),
            data=data,
        )
        _ = outputs
        html = render_html(data, [])
        return HTMLResponse(content=html)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
