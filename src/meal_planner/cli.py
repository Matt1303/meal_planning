from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from meal_planner.config import Settings, settings_to_redacted_dict
from meal_planner.db import get_engine, wait_for_db
from meal_planner.db.parse_repo import delete_override, list_overrides, upsert_override
from meal_planner.logging import configure as configure_logging
from meal_planner.report import ReportFormats

app = typer.Typer(help="Plant-based weekly meal planner CLI.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration commands")
db_app = typer.Typer(help="Database commands")
override_app = typer.Typer(help="Manage parser overrides")
plan_app = typer.Typer(help="Plan inspection commands")
nutrition_app = typer.Typer(help="Nutrition data commands")
data_app = typer.Typer(help="Data files commands")

app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(override_app, name="override")
app.add_typer(plan_app, name="plan")
app.add_typer(nutrition_app, name="nutrition")
app.add_typer(data_app, name="data")


@app.callback()
def _main(
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level"),
    log_format: str | None = typer.Option(None, "--log-format", help="json or console"),
) -> None:
    fmt: str | None = log_format
    if fmt not in (None, "json", "console"):
        raise typer.BadParameter("log_format must be 'json' or 'console'")
    configure_logging(log_level, fmt)  # type: ignore[arg-type]


@app.command("run")
def run(
    config: Path = typer.Option(Path("config/pipeline.yaml"), "--config", help="Config path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate config and DB only"),
    ignore_coverage: bool = typer.Option(
        False, "--ignore-coverage", help="Skip nutrition coverage gate"
    ),
    include_non_plant: bool = typer.Option(
        False, "--include-non-plant", help="Allow non-plant recipes"
    ),
    output_dir: Path = typer.Option(Path("reports"), "--output-dir", help="Where to write reports"),
    formats: str = typer.Option("md", "--formats", help="Comma-separated: md,html,docx"),
) -> None:
    settings = Settings.load(config)
    if include_non_plant:
        settings = settings.model_copy(
            update={"optimizer": settings.optimizer.model_copy(update={"include_non_plant": True})}
        )
    engine = get_engine()
    if not wait_for_db(engine):
        typer.echo("database not reachable", err=True)
        raise typer.Exit(code=2)
    if dry_run:
        typer.echo("dry-run ok")
        return
    fmt_set = {f.strip() for f in formats.split(",") if f.strip()}
    fmts = ReportFormats(
        md="md" in fmt_set,
        html="html" in fmt_set,
        docx="docx" in fmt_set,
    )
    from meal_planner.run_pipeline import run_all

    result = run_all(
        settings,
        engine=engine,
        ignore_coverage=ignore_coverage,
        formats=fmts,
        output_dir=output_dir,
    )
    typer.echo(f"plan_run_id={result.plan_run_id} correlation_id={result.correlation_id}")


@app.command("ingest")
def ingest(config: Path = typer.Option(Path("config/pipeline.yaml"), "--config")) -> None:
    from meal_planner.ingest import ingest_local_html

    settings = Settings.load(config)
    result = ingest_local_html(settings)
    typer.echo(
        f"files={result.files_seen} recipes={result.recipes_upserted} "
        f"ingredients={result.ingredients_inserted} non_plant={result.non_plant_filtered}"
    )


@app.command("parse")
def parse_cmd(config: Path = typer.Option(Path("config/pipeline.yaml"), "--config")) -> None:
    from meal_planner.parse import parse_ingredients
    from meal_planner.review import unreviewed_recipes

    settings = Settings.load(config)
    total = parse_ingredients(settings)
    typer.echo(f"parsed={total}")

    # A recipe added or edited since the last heading review has lines nothing
    # can classify — "Polenta" reads as an ingredient and gets a default
    # portion. Say so rather than let it quietly inflate a plan.
    pending = [
        r
        for r in unreviewed_recipes(get_engine(), settings.parse.review_state_path)
        if r.needs_attention
    ]
    if pending:
        typer.echo(
            f"\n{len(pending)} recipe(s) have quantity-less lines not yet reviewed for "
            f"section headings — run 'meal-planner review' to list them."
        )


@app.command("review")
def review_cmd(
    config: Path = typer.Option(Path("config/pipeline.yaml"), "--config"),
    accept: bool = typer.Option(False, "--accept", help="Record the current lines as reviewed"),
) -> None:
    """List recipes whose quantity-less lines have not been reviewed for headings."""
    from meal_planner.review import save_review_state, unreviewed_recipes

    settings = Settings.load(config)
    pending = unreviewed_recipes(get_engine(), settings.parse.review_state_path)
    flagged = [r for r in pending if r.needs_attention]
    for recipe in flagged:
        typer.echo(f"\n{recipe.title}")
        for line in recipe.quantityless_lines:
            typer.echo(f"    {line}")
    typer.echo(f"\n{len(flagged)} recipe(s) to review, {len(pending)} changed in total.")
    if accept:
        state = {r.title: r.lines_hash for r in pending}
        existing = settings.parse.review_state_path
        from meal_planner.review import load_review_state

        merged = {**load_review_state(existing), **state}
        save_review_state(existing, merged)
        typer.echo(f"recorded {len(state)} recipe(s) as reviewed")


@app.command("optimise")
def optimise(
    config: Path = typer.Option(Path("config/pipeline.yaml"), "--config"),
    include_non_plant: bool = typer.Option(False, "--include-non-plant"),
) -> None:
    from meal_planner.optimize import optimize_plan, write_plan

    settings = Settings.load(config)
    if include_non_plant:
        settings = settings.model_copy(
            update={"optimizer": settings.optimizer.model_copy(update={"include_non_plant": True})}
        )
    result = optimize_plan(settings)
    plan_run_id = write_plan(settings, result)
    typer.echo(f"plan_run_id={plan_run_id} relaxation={result.relaxation_level}")


@app.command("report")
def report(
    plan_run_id: int = typer.Option(..., "--plan-run-id"),
    output_dir: Path = typer.Option(Path("reports"), "--output-dir"),
    formats: str = typer.Option("md", "--format"),
) -> None:
    fmt_set = {f.strip() for f in formats.split(",") if f.strip()}
    outputs = _generate_report(plan_run_id, output_dir, fmt_set)
    for name, path in outputs.items():
        typer.echo(f"{name}={path}")


@app.command("health")
def health() -> None:
    engine = get_engine()
    ok = wait_for_db(engine, retries=3, delay=1)
    payload = {"db": "ok" if ok else "unreachable"}
    typer.echo(json.dumps(payload))
    if not ok:
        raise typer.Exit(code=1)


def _open_when_serving(url: str, address: str, port: int, timeout: float = 30.0) -> None:
    """Open the browser once the server accepts connections.

    Streamlit only opens a browser itself when it isn't headless, but headless
    is what stops its first-run email prompt blocking startup. So keep the
    prompt suppressed and do the opening here, once there's something to show.
    """
    import socket
    import time
    import webbrowser

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((address, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.3)


@app.command("ui")
def ui(
    port: int = typer.Option(8501, "--port"),
    address: str = typer.Option("127.0.0.1", "--address"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open a browser once ready"),
) -> None:
    import os
    import subprocess
    import sys
    import threading
    from importlib.util import find_spec

    if find_spec("streamlit") is None:
        typer.echo('streamlit is not installed. Run: pip install -e ".[ui]"', err=True)
        raise typer.Exit(code=1)

    repo_root = Path(__file__).resolve().parents[2]
    app_path = repo_root / "src" / "meal_planner" / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        address,
        "--server.port",
        str(port),
        # Headless suppresses Streamlit's first-run email prompt, which
        # otherwise blocks startup. The browser is opened below instead.
        "--server.headless",
        "true",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(repo_root / "src"))

    url = f"http://{address}:{port}"
    typer.echo(f"Starting the dashboard at {url}")
    if open_browser:
        threading.Thread(target=_open_when_serving, args=(url, address, port), daemon=True).start()
    raise typer.Exit(code=subprocess.run(cmd, env=env, check=False).returncode)


@config_app.command("validate")
def config_validate(path: Path = typer.Option(Path("config/pipeline.yaml"), "--config")) -> None:
    Settings.load(path)
    typer.echo("OK")


@config_app.command("print")
def config_print(
    path: Path = typer.Option(Path("config/pipeline.yaml"), "--config"),
    fmt: str = typer.Option("yaml", "--format"),
) -> None:
    settings = Settings.load(path)
    redacted = settings_to_redacted_dict(settings)
    if fmt == "json":
        typer.echo(json.dumps(redacted, indent=2))
    else:
        import yaml

        typer.echo(yaml.safe_dump(redacted, sort_keys=False))


@db_app.command("check")
def db_check() -> None:
    engine = get_engine()
    ok = wait_for_db(engine, retries=3, delay=1)
    if not ok:
        typer.echo("db: unreachable", err=True)
        raise typer.Exit(code=1)
    typer.echo("db: ok")


@override_app.command("add")
def override_add(
    raw: str = typer.Option(...),
    canonical: str = typer.Option(...),
    group: str = typer.Option(...),
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        upsert_override(conn, raw_text=raw, canonical=canonical, food_group=group)
    typer.echo("ok")


@override_app.command("list")
def override_list() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = list_overrides(conn)
    for raw, canonical, group in rows:
        typer.echo(f"{raw} -> {canonical} ({group})")


@override_app.command("remove")
def override_remove(raw: str = typer.Option(...)) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        deleted = delete_override(conn, raw)
    typer.echo(f"deleted={deleted}")


@plan_app.command("show")
def plan_show(plan_run_id: int | None = typer.Option(None, "--plan-run-id")) -> None:
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        if plan_run_id is None:
            row = conn.execute(
                text(
                    "SELECT plan_run_id FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 1"
                )
            ).fetchone()
            if row is None:
                typer.echo("no plan runs", err=True)
                raise typer.Exit(code=1)
            plan_run_id = int(row[0])
        rows = conn.execute(
            text(
                """
                SELECT day, meal_type, COALESCE(r.title, '(none)') AS title
                FROM meal_planning.plan_meal pm
                LEFT JOIN meal_planning.recipe r ON r.recipe_id = pm.recipe_id
                WHERE plan_run_id = :pr
                ORDER BY day, meal_type
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()
    typer.echo(f"plan_run_id={plan_run_id}")
    for day, meal_type, title in rows:
        typer.echo(f"  day={day} {meal_type}: {title}")


@plan_app.command("list")
def plan_list(limit: int = typer.Option(10, "--limit")) -> None:
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT plan_run_id, run_time, status, solver_status, slack_total
                FROM meal_planning.plan_run
                ORDER BY run_time DESC
                LIMIT :n
                """
            ),
            {"n": limit},
        ).fetchall()
    for row in rows:
        typer.echo(f"{row[0]} {row[1]} {row[2]} {row[3]} slack={row[4]}")


@nutrition_app.command("refresh")
def nutrition_refresh(
    ingredient: str | None = typer.Option(None, "--ingredient"),
    refresh_all: bool = typer.Option(False, "--all"),
) -> None:
    from sqlalchemy import text

    engine = get_engine()
    with engine.begin() as conn:
        if refresh_all:
            confirm = typer.confirm("Drop all cached nutrition?")
            if not confirm:
                raise typer.Exit(code=1)
            conn.execute(text("DELETE FROM meal_planning.ingredient_nutrition_cache"))
            typer.echo("nutrition cache cleared")
            return
        if ingredient is None:
            typer.echo("--ingredient or --all required", err=True)
            raise typer.Exit(code=2)
        conn.execute(
            text(
                "DELETE FROM meal_planning.ingredient_nutrition_cache WHERE ingredient_canonical = :i"
            ),
            {"i": ingredient},
        )
    typer.echo(f"refreshed={ingredient}")


@data_app.command("fetch-cofid")
def fetch_cofid(
    config: Path = typer.Option(Path("config/pipeline.yaml"), "--config"),
    dest: Path | None = typer.Option(None, "--dest"),
) -> None:
    from meal_planner.nutrition import fetch_cofid as do_fetch

    settings = Settings.load(config)
    target = dest or settings.nutrition.cofid_path
    if target is None:
        typer.echo("no cofid path configured", err=True)
        raise typer.Exit(code=2)
    if not settings.nutrition.cofid_url:
        typer.echo("no cofid url configured", err=True)
        raise typer.Exit(code=2)
    out = do_fetch(settings.nutrition.cofid_url, target)
    typer.echo(f"saved={out}")


def _generate_report(plan_run_id: int, output_dir: Path, fmt_set: set[str]) -> dict[str, Path]:
    from meal_planner.report import generate_report

    fmts = ReportFormats(
        md="md" in fmt_set,
        html="html" in fmt_set,
        docx="docx" in fmt_set,
    )
    return generate_report(plan_run_id, output_dir=output_dir, formats=fmts)


def main() -> int:
    try:
        app()
        return 0
    except typer.Exit as exc:
        return exc.exit_code
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
