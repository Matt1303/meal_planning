# meal-planner

A plant-based weekly meal planner. Ingests recipe HTML, parses ingredients,
enriches them with nutrition data, and runs a multi-objective optimiser to
produce a weekly plan that respects Daily Dozen targets, calorie/fiber
ranges, ratings, and recency.

## Quickstart

```bash
git clone <repo> meal-planning && cd meal-planning
cp .env.example .env

# 1. install (with uv or pip)
uv sync --all-extras   # or:  pip install -e ".[dev,report,llm-anthropic,llm-openai,serve]"

# 2. start postgres
docker-compose up -d postgres

# 3. apply migrations
make migrate

# 4. run a plan
meal-planner run
```

The CLI prints a `plan_run_id`; reports land in `reports/`.

## Repository layout

```
src/meal_planner/        package source (config, ingest, parse, nutrition, optimize, report, llm, cli)
config/                  pipeline.yaml + canonical food list, synonyms, units, density, non-plant terms
alembic/                 schema migrations (head includes initial schema, legacy drop, reporting views)
flows/                   prefect flow + declarative deployment
tests/unit/              fast unit tests (no DB)
tests/integration/       integration tests (testcontainers postgres)
tests/fixtures/          deterministic recipe HTML + parser golden inputs
ops/                     cron wrapper, grafana dashboard
docs/                    architecture, configuration, metrics, runbooks
archive/                 retired legacy stack (kept for reference until a release)
```

## Common tasks

| What | Command |
|---|---|
| run end-to-end pipeline | `meal-planner run` |
| validate config | `meal-planner config validate` |
| print resolved config | `meal-planner config print` |
| add a parser override | `meal-planner override add --raw "..." --canonical "..." --group "..."` |
| inspect latest plan | `meal-planner plan show` |
| list recent runs | `meal-planner plan list --limit 10` |
| refresh nutrition cache | `meal-planner nutrition refresh --ingredient kale` |
| serve dashboard | `python -m meal_planner.serve` (needs `serve` extras) |
| typecheck | `make typecheck` |
| run tests | `make test-unit` (fast) or `make test` (full) |
| start scheduler | `prefect deploy && prefect work-pool create default && prefect worker start --pool default` |

See [docs/architecture.md](docs/architecture.md), [docs/configuration.md](docs/configuration.md),
[docs/metrics.md](docs/metrics.md), and [docs/runbooks/](docs/runbooks/).

## Background

Plan and migration history is in:
- [plan.md](plan.md) — original plan, fully delivered
- [research_v2.md](research_v2.md) — deep-dive research that drove the rebuild
- [plan_v2.md](plan_v2.md) — overhaul plan executed in this codebase
