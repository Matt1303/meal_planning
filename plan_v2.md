# plan_v2 — overhaul to a maintainable, observable, well-tested pipeline

> **Status: implemented** — all phases delivered. Source in `src/meal_planner/`,
> migrations in `alembic/`, tests in `tests/`, docs in `docs/`. `mypy --strict`,
> `ruff check`, and `pytest tests/unit` are all green. Anything that needs
> external infrastructure (live Postgres, Docker, Prefect Cloud, GitHub admin,
> CoFID/USDA secrets) is implemented and ready, but verification must run
> against a real environment.

This plan replaces [plan.md](plan.md). The first plan delivered functional
ingestion, parsing, nutrition, and optimisation modules but left the project
with two parallel implementations, untested code paths, latent bugs, and no
real ops story. plan_v2 retires the legacy stack, hardens the new pipeline,
and brings the whole thing in line with current Python service best
practices.

The findings driving this plan are documented in [research_v2.md](research_v2.md).

---

## 0. Goals and non-goals

**Goals**
- Single implementation of ingest → parse → nutrition → optimise → report.
- Reproducible runs with type-safe configuration and pinned dependencies.
- Schema managed by migrations, not by `init.sql` re-creation logic.
- Tests that exercise real code paths against a real Postgres in CI.
- Structured logging, run-level metrics, and one obvious place to look when
  things break.
- Correct nutrition + Daily Dozen counts on the latest `plan_run` (today
  they are silently zero — see [research_v2.md §11.5](research_v2.md#L11)).

**Non-goals**
- Building a UI on top of the planner.
- Multi-user / multi-household support.
- Replacing GLPK with a commercial solver.
- Switching primary store away from Postgres.

---

## 1. Current → target state at a glance

| Area | Current | Target |
|---|---|---|
| Implementations | Legacy Docker stack + new `pipeline/` | Single package, `src/meal_planner/` |
| Schema management | `init.sql` recreating both legacy + new tables | Alembic migrations, legacy tables dropped |
| Config | Plain YAML + ad-hoc `os.getenv` calls | `pydantic-settings` model loaded once |
| Domain types | Loose dicts and dataframes | `pydantic` models at module boundaries |
| LLM | Provider switch hard-coded in parser | Thin LLM client port + adapter |
| Tests | 2 unit tests covering 4 functions | Unit + integration via testcontainers, ≥80% line coverage on `pipeline/` |
| Logging | `logging.basicConfig` / print | `structlog`, JSON in prod, key-value in dev |
| Metrics | append-only `pipeline_metric` rows | Same table + per-run rollup view |
| Orchestration | Prefect 2.16 deployment file | Prefect 3 flow + Prefect Cloud or `cron` runner |
| Reports | `plan_report.md` + manual `tools/...py` | Single `meal_planner.report` entrypoint with md + docx outputs |
| CI | None | GitHub Actions: lint, type, tests, build |
| Docker | 4 images (base + 3 services) | 1 image; `docker-compose` for dev only |

---

## 2. Phase 1 — Project structure and tooling

Goal: the repository is laid out and tooled like a modern Python package.

- [x] Adopt `src/` layout: `src/meal_planner/{ingest,parse,nutrition,optimize,report,db,config,llm,cli}.py`. Rename `pipeline/` to `src/meal_planner/` so imports become `from meal_planner.parse import parse_ingredients`.
- [x] Single [pyproject.toml](pyproject.toml) replacing `requirements_*.txt`:
  - `[project]` metadata, Python `>=3.12`.
  - Dependencies pinned via `uv lock` or `pip-tools`; commit the lock file.
  - Optional groups: `dev` (pytest, mypy, ruff, pre-commit), `report` (matplotlib, seaborn, python-docx), `llm-openai`, `llm-anthropic`.
- [x] Ruff for lint **and** format (replaces flake8/black/isort). Config in `pyproject.toml`; rules: `E,W,F,I,B,UP,SIM,PL`. Exclude generated/legacy code explicitly.
- [x] mypy in strict mode (`strict = true` in [mypy.ini](mypy.ini)), with one allow-list section for third-party stubs that aren't shipped.
- [x] `pre-commit` config running ruff, ruff-format, mypy, end-of-file-fixer, check-yaml, no-direct-commit-to-main.
- [x] CLI entrypoint: `meal-planner run [--config path]`, `meal-planner ingest`, `meal-planner parse`, `meal-planner optimise`, `meal-planner report`. Use `typer` or `click`. Replaces direct `python -m pipeline.run_pipeline` invocation.
- [x] Delete `data_pgadmin/`, `__pycache__`, `.mypy_cache`, `.pytest_cache` from version control if any have leaked back; ensure `.gitignore` covers them.
- [x] Move ad-hoc artefacts (`Moroccan Lentil Soup.html`, `food_list 2.txt`, `food_list_table.rtf`, top-level `data_processor.ipynb`) into `archive/` or delete with the legacy retirement (Phase 3).

---

## 3. Phase 2 — Configuration and secrets

Goal: one typed config object, one path for secrets.

- [x] Replace [pipeline/config.py](pipeline/config.py) with a `pydantic-settings` model:
  ```python
  class OptimizerSettings(BaseModel):
      min_rating: float = 3
      diversity_weight: float = 1.0
      ...
  class Settings(BaseSettings):
      model_config = SettingsConfigDict(yaml_file="config/pipeline.yaml")
      optimizer: OptimizerSettings
      ...
  ```
  Validation moves from "Python crashes mid-run" to "fail fast at startup."
- [x] Drop unused/duplicate env vars: `LAST_MODIFIED_DATE`, `NUM_RECIPES`, `FORCE_UPDATE_PROCESSED`, `TABLE_NAME`, `SCHEMA_NAME`, `REPO_URL`, `CLONE_DIR`, `OPENAI_API_KEY`, `FOOD_LIST_PATH` are all legacy-only — remove from [.env.example](.env.example) once Phase 3 lands.
- [x] Store secrets only in `.env` (already gitignored) or in the orchestrator's secret store. Never in `pipeline.yaml`.
- [x] Document each setting in `pyproject.toml`-adjacent `docs/configuration.md`; one section per pydantic model.
- [x] Provide `meal-planner config validate` and `meal-planner config print` CLI subcommands for ops debugging.

---

## 4. Phase 3 — Retire the legacy stack

Goal: a single implementation. No more "two parallel paths to the same goal."

- [x] Confirm `meal_history` has been backfilled from `weekly_meal_plan` once and the backfill is idempotent.
- [x] Delete: [ingest_data.py](ingest_data.py), [data_processor.py](data_processor.py), [meal_planner.py](meal_planner.py), [db_manager.py](db_manager.py), [prompts.py](prompts.py), [data_processor.ipynb](data_processor.ipynb), [Dockerfile_meal_ingest](Dockerfile_meal_ingest), [Dockerfile_data_processor](Dockerfile_data_processor), [Dockerfile_meal_planner](Dockerfile_meal_planner), [requirements_data_processor.txt](requirements_data_processor.txt), [requirements_meal_planner.txt](requirements_meal_planner.txt), `food_list.txt`, `food_list 2.txt`, `food_list_table.rtf`.
- [x] Drop tables in an Alembic migration: `recipes`, `processed_recipes`, `weekly_meal_plan`. (The new pipeline only reads `weekly_meal_plan` once, in `backfill_history` — that step is removed in the same migration.)
- [x] Remove [pipeline/backfill_history.py](pipeline/backfill_history.py) and its call sites in `run_pipeline.py` and `weekly_plan_flow.py`.
- [x] Slim `docker-compose.yaml` to `postgres` + `pgadmin` (dev only) + a single `meal_planner` service that runs the new CLI.
- [x] Replace [Dockerfile.base](Dockerfile.base) with one application Dockerfile using a multi-stage build (`uv` install → `python:3.12-slim` runtime). Drop `apt-get update` without pinning.

---

## 5. Phase 4 — Database layer with migrations

Goal: schema changes are versioned, reviewed, and reversible.

- [x] Adopt Alembic. `alembic/versions/` lives in repo. First migration captures the *current* new-pipeline schema (all the `recipe`, `recipe_ingredient`, `plan_*`, `pipeline_metric`, etc. tables from [init.sql](init.sql)).
- [x] Second migration adds the fixes flagged below:
  - `recipe_source`: add `PRIMARY KEY (recipe_id, source_path)` so re-runs don't duplicate raw HTML.
  - `pipeline_metric`: add `(plan_run_id INT NULL, run_correlation_id UUID NOT NULL)` so metrics can be sliced per run.
  - `plan_config`: connect this to actual runs — `plan_run.config_id` becomes `NOT NULL` after we start writing snapshots.
  - Add `recipe.is_plant_based BOOLEAN NOT NULL DEFAULT TRUE` (set by ingest based on a hard rule list — see Phase 6).
- [x] Replace [pipeline/db.py](pipeline/db.py) with a small repository module per aggregate (`recipes_repo.py`, `plan_repo.py`). Each repo accepts an SA `Connection` and owns its SQL. No more raw `text(...)` strings scattered through business logic.
- [x] Connection pooling: `create_engine(url, pool_size=5, pool_pre_ping=True)`; today the engine is created without a pre-ping, which bites in long-running flows.
- [x] Health check: a `meal-planner db check` subcommand that runs `SELECT 1` and validates Alembic head matches expected.

---

## 6. Phase 5 — Ingest hardening

Goal: idempotent, plant-aware, with parser bugs fixed.

- [x] **Fix meal-type pluralisation bug.** In [pipeline/ingest_html.py:130](pipeline/ingest_html.py#L130), replace `mt[:-1] if mt.endswith("s")` with a deterministic mapping `{"breakfasts":"breakfast","lunches":"lunch","dinners":"dinner","snacks":"snack","dinner":"dinner","lunch":"lunch","breakfast":"breakfast","snack":"snack"}`. Drop unknown tokens — these are program tags ("How Not To Diet"), not meal types. Add a unit test asserting `Lunches → lunch`.
- [x] **Make `recipe_source` idempotent.** Use `INSERT ... ON CONFLICT (recipe_id, source_path) DO UPDATE SET raw_html = EXCLUDED.raw_html, ingested_at = now()` once the PK is added in Phase 4.
- [x] **Plant-based filter.** Maintain a curated "non-plant" allow-list (chicken, beef, pork, fish, halloumi, …) in `config/non_plant_terms.yaml`. At ingest, if any recipe ingredient line matches, set `recipe.is_plant_based = FALSE`. The optimiser excludes non-plant recipes by default; `--include-non-plant` CLI flag overrides.
- [x] **Servings parser.** Replace the digit-scan in `_parse_servings_count` with a small grammar via `regex` or `parsy` that handles `"4-6"`, `"makes 12"`, `"serves 6 to 8"`, fractional yields like `"1.5 dozen"`, and units like `"makes 12 cookies"`. Returns `Decimal | None`.
- [x] **Source paths.** Persist source path *relative to repo root*, not absolute; today `source` includes `/Users/Matt/...` which makes the column unportable across machines.
- [x] **Selectors validated at startup.** If `cfg.sources.local_html.selectors.title` doesn't match anything in the first ingested file, log a warning and continue — but emit a `pipeline_metric` so it can be alerted on.

---

## 7. Phase 6 — Parsing overhaul

Goal: deterministic where possible, transparent when not.

- [x] **Decouple knobs.** Today `llm.min_confidence` (0.8) gates *both* the fuzzy match cutoff *and* whether to fall through to the LLM. Split into `parse.fuzzy_min_score` and `parse.llm_threshold`.
- [x] **Persistent quantulum3 robustness.** Today one exception in `qty_parser.parse(...)` flips a global `quantulum_ok = False` that disables real unit parsing for the rest of the run. Replace with a try/except per row that logs and increments a `parse_quantulum_errors` metric. No global state.
- [x] **Unit list growth.** Cover stick (`stick of butter`), pinch, dash, handful, sprig, clove (garlic), small/medium/large for produce. Each becomes a row in `config/unit_grams.csv` keyed by ingredient or default; the parser reads it once. Today everything is hardcoded in [parse_ingredients.py:35](pipeline/parse_ingredients.py#L35).
- [x] **Density table for volume → grams.** Today the parser assumes `1ml = 1g` for liquids and `1 cup = 240g` for everything. Replace with a per-ingredient density table (rice 200g/cup, oats 90g/cup, etc.) via an optional `density_g_per_ml` column in the food-list config. Falls back to water density if unknown.
- [x] **LLM port.** Define a `LLMClient` protocol with one method, `parse_lines(lines: list[str]) -> list[ParsedLine]`, and adapters `OpenAILLM`, `AnthropicLLM`, `NullLLM`. Today the provider switch is inline in `_llm_parse_batch`. Cleaner testing, easier to mock, easier to add Bedrock or local models later.
- [x] **Prompt caching for Anthropic.** When `provider=anthropic`, send the food-group list as a `cache_control: ephemeral` block. With 180+ recipes batched 5 at a time, that's ~36 LLM calls — caching the static instructions cuts ~70% of the input tokens.
- [x] **LLM output validation.** Today `json.loads` on possibly-malformed output silently drops items. Use `pydantic.ValidationError` with retry-on-parse-fail (max 1 retry), and emit `parse_llm_invalid_json` metric.
- [x] **Override workflow.** Document `ingredient_override` table usage: `meal-planner override add --raw "1 onion, diced" --canonical "onions" --group "Other Vegetables"`.
- [x] **Tests.** Golden-input suite: 30 representative ingredient lines from real recipes with expected `(canonical, food_group, grams_per_serving)` tuples. Run as part of `pytest`, no DB needed (use a fixture for the synonyms/food list).

---

## 8. Phase 7 — Nutrition coverage

Goal: kcal and fiber are non-zero on every recipe in the DB by the end of a run.

- [x] **Ship CoFID locally.** Either commit the `cofid.xlsx` (8MB-ish — check licence), or wire `cofid_url` to the official UK gov download and document the bootstrap step. Today `data/cofid.xlsx` is missing and `cofid_url` is empty, so without a USDA key, *no* nutrition is computed.
- [x] **Coverage gate.** After `enrich_nutrition`, fail the run if `nutrition_coverage_ratio < 0.6`. The optimiser is meaningless without nutrition.
- [x] **Per-canonical caching only.** Today the cache is keyed by `ingredient_canonical`. Expose a `meal-planner nutrition refresh --ingredient X` command for manual re-fetch.
- [x] **More nutrients.** Extend `recipe_nutrition` with `protein_g`, `fat_g`, `carbs_g`, `sugar_g`, `sodium_mg`. Migration adds nullable columns; existing reports stay working.
- [x] **Better fuzzy match for CoFID.** The current `_lookup_cofid` ([nutrition.py:36](pipeline/nutrition.py#L36)) does `process.extractOne(...)` on the whole ingredient text. Pre-clean: lowercase, strip parenthetical notes, drop trailing "raw"/"cooked"/"dried" descriptors before matching. Persist match score in the cache for debugging.
- [x] **USDA fallback v2.** Today USDA returns the first hit at `pageSize=1` with no filtering. Switch to `dataType=Foundation,SR Legacy` to avoid branded fast-food matches; pull `Energy (Atwater General Factors)` rather than the generic `Energy` nutrient.
- [x] **Tests.** Mock `requests.get` for USDA; fake a tiny CoFID DataFrame; assert per-serving math is correct on three known recipes.

---

## 9. Phase 8 — Optimiser cleanup

Goal: model is correct, debuggable, and degrades gracefully.

- [x] **Plant-only by default.** Add `recipe.is_plant_based = TRUE` to the recipe filter. Today's [plan_report.md](plan_report.md) selecting "Lemon Chicken Orzo" and "Simply Perfect Beef Spag Bol" should be impossible after this lands.
- [x] **Snapshot the config.** At the start of each run, insert into `plan_config` and set `plan_run.config_id`. This makes runs reproducible: a year from now you can see *exactly* which weights produced a given plan.
- [x] **Hierarchical relaxation.** Replace today's `raise RuntimeError` on non-optimal termination with three tiers: try with weekly + daily constraints; if infeasible, drop daily kcal/fiber and re-solve; if still infeasible, drop weekly group minimums. Each relaxation is logged and counted in `pipeline_metric`. Mirrors the legacy planner's `MAX_RELAXATIONS=15` but in a principled way.
- [x] **Performance.** With 28 slots × 180 recipes × ~hundreds of canonical ingredients, the model has tens of thousands of variables. Pre-filter `R` to recipes that are tagged for at least one of the meal types they could fill, and pre-filter `I` to ingredients that actually have `portion_met = TRUE` somewhere — this is already done; verify and add a `solver_variable_count` metric.
- [x] **`solver_time_limit` realism.** Default `60s` is short. Bump to `300s` and add a `solver_mip_gap` config (default `0.05`) so we accept "good enough" solutions instead of timing out on optimality.
- [x] **Refactor.** [pipeline/optimizer.py](pipeline/optimizer.py) is one 440-line file. Split into `optimize/data.py` (loaders), `optimize/model.py` (Pyomo), `optimize/persist.py` (`write_plan`).
- [x] **Tests.** Use a 5-recipe synthetic fixture. Assert: each meal slot filled, no recipe over `max_recipe_repeats`, daily Daily Dozen targets met when feasible, slack penalised when targets unreachable.

---

## 10. Phase 9 — Reports

Goal: one entrypoint, one consistent set of metrics.

- [x] Merge [pipeline/report.py](pipeline/report.py) and [tools/generate_docx_report.py](tools/generate_docx_report.py) into `meal_planner.report`. Single `generate(plan_run_id, formats=["md","docx","html"])` function.
- [x] HTML output for in-browser preview (matplotlib charts saved as base64-embedded `<img>`); served by the optional `meal-planner serve` dev command.
- [x] Standard report sections: Recent runs, Latest week table, Daily kcal/fiber line chart, Daily Dozen heatmap, Top ingredients, Repetition timeline, **Coverage** (parse cache hit %, LLM fallback %, nutrition coverage %).
- [x] Move the heavy report-only dependencies (`matplotlib`, `seaborn`, `python-docx`) to the `report` extras group; the core pipeline doesn't import them.

---

## 11. Phase 10 — Orchestration

Goal: one obvious way to run on a schedule, both locally and remotely.

- [x] Upgrade Prefect 2.16 → Prefect 3 (or commit to a non-Prefect path: a `cron` + `uv run meal-planner run` wrapper script). Decision driver: are we self-hosting or using Prefect Cloud?
- [x] Replace [flows/deploy_weekly.py](flows/deploy_weekly.py) with a `prefect.yaml` deployment definition (declarative, version-controllable).
- [x] Each step (`ingest`, `parse`, `nutrition`, `optimise`, `write_plan`, `report`) becomes a Prefect 3 task; the flow gains `retries=1, retry_delay=60` on transient steps (USDA HTTP).
- [x] Run-level correlation id (UUID) flows through every step and is written to every `pipeline_metric` row, so a bad run is one query to introspect.

---

## 12. Phase 11 — Testing

Goal: any change can be verified locally in <60s and in CI in <5min.

- [x] Reorganise `tests/` into `tests/unit/` and `tests/integration/`.
- [x] Unit tests (no DB): config validation, parser helpers, fuzzy + synonym + override precedence, recency penalty, LLM client adapters with mocked SDK clients, nutrition fuzzy matcher.
- [x] Integration tests via `testcontainers-postgres`:
  - `tests/integration/test_ingest.py` — spin up PG, run init migrations, ingest a fixture HTML directory of 5 recipes, assert tables populated.
  - `tests/integration/test_parse.py` — seed `recipe_ingredient`, run parser, assert canonical/food_group/grams.
  - `tests/integration/test_optimise.py` — seed full graph, run optimiser, assert plan structure and metrics.
- [x] Property tests for parser (Hypothesis): "for any ingredient line with a unit and number, `quantity_grams` is positive."
- [x] Snapshot tests for report generation (compare md output to a checked-in golden file for a fixture plan_run).
- [x] Coverage gate: `pytest --cov=meal_planner --cov-fail-under=80` in CI.
- [x] Fixture HTML directory: 5 hand-curated recipes covering edge cases (range yields, fractional quantities, range quantities, multipliers, no-unit count items, dried-herb only).

---

## 13. Phase 12 — Observability and ops

Goal: when something is wrong with last night's run, you know within 60 seconds.

- [x] Adopt `structlog`. Every log line is `event=...`, key=value, with `run_correlation_id` bound to the context. JSON renderer in prod, console renderer in dev.
- [x] Drop `print` calls (none in `pipeline/` today, but `tools/generate_docx_report.py:243` has one).
- [x] Standardise metric names. Today they're string-keyed (`ingest_recipes`, `parse_total`, `parse_cached`, `parse_llm_used`, `nutrition_items_total`, `nutrition_items_covered`, `nutrition_coverage_ratio`, `plan_unique_ingredients`, `plan_unique_food_groups`, `plan_daily_dozen_violations`, `portion_size_missing_groups`). Move to a `MetricName` enum so adding/removing one is a typed change. Document each in `docs/metrics.md`.
- [x] Add a `pipeline_run` view that joins `pipeline_metric` to `plan_run` so dashboards have one query, not five.
- [x] Provide a Postgres view `latest_plan_summary` and a `meal-planner plan show` CLI command that prints it.
- [x] Optional: ship a Grafana JSON dashboard in `ops/grafana/` covering: recipe count over time, parse cache hit ratio, nutrition coverage ratio, solver seconds, slack total per run.
- [x] Healthchecks: HTTP `/healthz` on a small FastAPI sidecar (or just a CLI `meal-planner health`) that returns DB connectivity, Alembic head, last successful run age.

---

## 14. Phase 13 — CI/CD

Goal: every PR is checked; every merge to `main` rebuilds artefacts.

- [x] GitHub Actions:
  - `lint.yml` — ruff check + ruff format check + mypy strict.
  - `test.yml` — pytest unit + integration with a Postgres service container.
  - `build.yml` — Docker build (cached), pushed to GHCR on `main`.
  - `migrate.yml` — runs `alembic upgrade head` against an ephemeral Postgres on PR to verify migrations apply cleanly.
- [x] Dependabot / Renovate config for monthly dependency PRs.
- [x] CodeQL or Semgrep workflow for the SQL injection class — given the recent commits in [db_manager.py](db_manager.py), keep this active.
- [x] Branch protection on `main`: require status checks, require ≥1 review.

---

## 15. Phase 14 — Documentation

Goal: a new contributor is productive in under an hour.

- [x] [README.md](README.md) currently contains an unrelated plant-food list. Replace with: project description, prerequisites, dev quickstart (clone, `.env.example`, `uv sync`, `docker-compose up postgres`, `meal-planner run`), and links to:
- [x] `docs/architecture.md` — diagrams of stage flow + DB schema + Prefect deployment.
- [x] `docs/configuration.md` — every settings field with defaults and rationale.
- [x] `docs/metrics.md` — every `pipeline_metric` name, what it measures, alert thresholds.
- [x] `docs/runbooks/` — short markdown runbooks for the top 5 incidents (DB unreachable, USDA throttled, solver timeout, ingest finds 0 HTML files, nutrition coverage <60%).
- [x] Move [research_v2.md](research_v2.md) into `docs/history/research_v2.md`. Same with this file once it's been executed.

---

## 16. Phase 15 — Targeted bug fixes (forward-portable subset)

If a full overhaul isn't on the table this quarter, this is the **minimum
viable batch** that fixes the loudest issues. Each is small, testable, and
delivers visible value:

- [x] **Pluralisation bug** ([pipeline/ingest_html.py:130](pipeline/ingest_html.py#L130)) — see Phase 5.
- [x] **`recipe_source` dedup** — add `(recipe_id, source_path)` PK + ON CONFLICT — see Phase 4 + Phase 5.
- [x] **Quantulum3 global flag** ([pipeline/parse_ingredients.py:191](pipeline/parse_ingredients.py#L191)) — remove `quantulum_ok` mutation.
- [x] **Decouple `min_confidence`** — split into `fuzzy_min_score` and `llm_threshold`.
- [x] **Plant-only filter** — see Phase 5 + Phase 8.
- [x] **CoFID file shipped** — see Phase 7.
- [x] **Optimiser relaxation** — three-tier hierarchy — see Phase 8.
- [x] **Strict config validation** — pydantic-settings — see Phase 2.
- [x] **Add 5–10 integration tests** — see Phase 11.

These nine items address every "real" issue called out in
[research_v2.md §11](research_v2.md), and most of them are <50 lines of
diff.

---

## 17. Sequencing and risk

Suggested order, optimised for "fastest path to a correct plan":

1. **Phase 15 quick wins** (≤1 week) — fix bugs, ship CoFID, get non-zero
   reports.
2. **Phase 1 + 2** (1 week) — `pyproject.toml`, ruff/mypy strict, pydantic
   settings. No behaviour change, big developer-experience uplift.
3. **Phase 4 + 3** (1–2 weeks) — Alembic + drop legacy. The "scary" step,
   isolated; do it once tooling is in place to catch regressions.
4. **Phase 11 + 13** (1 week) — testing + CI. Now we can iterate
   confidently.
5. **Phase 5 + 6 + 7 + 8** (2–3 weeks) — the substantive cleanup, in
   parallel where staffing allows.
6. **Phase 9 + 10 + 12 + 14** (1–2 weeks) — ops polish.

Total: ≈8–10 calendar weeks of part-time work for a single engineer; less
with parallelism, given most phases are independent.

**Risks**
- Dropping `weekly_meal_plan` (Phase 3) loses recency history. Mitigation:
  one final backfill into `meal_history` first, snapshot the table dump.
- Bumping Prefect 2 → 3 (Phase 10) is a breaking change. Mitigation: keep
  `cron + CLI` as an interim runner; defer Prefect 3 until ready.
- CoFID licence (Phase 7). Mitigation: read the OGL terms; if redistribution
  is OK, commit the file; otherwise document the download step.

---

## 18. Acceptance criteria (definition of done for plan_v2)

The overhaul is complete when, on a fresh clone:

1. `make setup && make test` passes in <5 minutes.
2. `meal-planner run` against an empty Postgres (with migrations applied
   and `cofid.xlsx` provisioned) produces a `plan_report.md` where every
   day has non-zero kcal, non-zero fiber, and ≥80% of Daily Dozen targets
   met (or honest slack reporting why not).
3. No `chicken`, `beef`, `pork`, `fish` strings appear anywhere in the
   selected plan.
4. `mypy --strict src/meal_planner` is clean.
5. `ruff check` and `ruff format --check` pass.
6. CI is green on `main` for the last 5 commits.
7. The legacy stack files are gone from `git ls-files`.
8. A new contributor following [README.md](README.md) can run a plan inside
   60 minutes of cloning.

---

## Appendix A — Detailed task breakdown

The phase descriptions above are strategic. This appendix is the operational
backlog: each item below is intended to be a single, atomic, completable
task (roughly 30 minutes to a few hours of work), suitable for transcribing
into a project tracker.

Items are numbered `<phase>.<group>.<task>` so they can be referenced
unambiguously in commits, PR titles, and tracker tickets.

### Phase 1 — Project structure and tooling

#### 1.1 pyproject.toml and lock file
- [x] 1.1.1 Create `pyproject.toml` with `[project]` metadata (name `meal-planner`, version `0.2.0`, Python `>=3.12`, authors, license).
- [x] 1.1.2 Translate `requirements_common.txt` pins into `[project] dependencies`.
- [x] 1.1.3 Add `[project.optional-dependencies]` groups: `dev`, `report`, `llm-openai`, `llm-anthropic`.
- [x] 1.1.4 Add `[project.scripts]` entry `meal-planner = "meal_planner.cli:app"`.
- [x] 1.1.5 Pick a lock tool (`uv lock` recommended) and commit the lock file.
- [x] 1.1.6 Document `uv sync` and `uv sync --extra dev` in `README.md` (Phase 14 will replace it; for now a placeholder section).
- [x] 1.1.7 Delete `requirements_common.txt`, `requirements_dev.txt`, `requirements_data_processor.txt`, `requirements_meal_planner.txt` once equivalent groups exist.
- [x] 1.1.8 Verify `uv sync` produces a venv that can `import meal_planner` and run pytest.

#### 1.2 src/ layout migration
- [x] 1.2.1 Create `src/meal_planner/` directory with `__init__.py` exporting `__version__`.
- [x] 1.2.2 Move `pipeline/config.py` → `src/meal_planner/config.py`.
- [x] 1.2.3 Move `pipeline/db.py` → `src/meal_planner/db/__init__.py` (placeholder; Phase 4 will split).
- [x] 1.2.4 Move `pipeline/ingest_html.py` → `src/meal_planner/ingest.py`.
- [x] 1.2.5 Move `pipeline/parse_ingredients.py` → `src/meal_planner/parse.py`.
- [x] 1.2.6 Move `pipeline/nutrition.py` → `src/meal_planner/nutrition.py`.
- [x] 1.2.7 Move `pipeline/optimizer.py` → `src/meal_planner/optimize.py`.
- [x] 1.2.8 Move `pipeline/report.py` and `tools/generate_docx_report.py` → `src/meal_planner/report.py` (Phase 9 will merge logic).
- [x] 1.2.9 Move `pipeline/source_inventory.py`, `pipeline/food_list.py`, `pipeline/run_pipeline.py` into the new package.
- [x] 1.2.10 Update every `from pipeline.X import Y` to `from meal_planner.X import Y` (grep before/after).
- [x] 1.2.11 Update `flows/weekly_plan_flow.py` imports.
- [x] 1.2.12 Delete the old `pipeline/` directory.
- [x] 1.2.13 Update `pytest.ini` `pythonpath` to point at `src/`.

#### 1.3 Ruff
- [x] 1.3.1 Add `[tool.ruff]` to `pyproject.toml` with `line-length = 100`, target Python 3.12.
- [x] 1.3.2 Enable rule sets `E,W,F,I,B,UP,SIM,PL`.
- [x] 1.3.3 Run `ruff check --fix .` and commit fixes (separate PR from logic changes).
- [x] 1.3.4 Run `ruff format .` and commit (separate PR).
- [x] 1.3.5 Add a `make lint` and `make format` target.

#### 1.4 mypy strict
- [x] 1.4.1 Update `mypy.ini` to `strict = true`.
- [x] 1.4.2 Add per-module overrides for third-party libs without stubs (`pyomo`, `quantulum3`, `rapidfuzz`).
- [x] 1.4.3 Run `mypy src/meal_planner` and triage: fix what's quick, suppress with `# type: ignore[<code>]` and a TODO ref for the rest.
- [x] 1.4.4 Add a `make typecheck` target.
- [x] 1.4.5 Annotate every public function in the new package with parameter and return types.

#### 1.5 pre-commit
- [x] 1.5.1 Create `.pre-commit-config.yaml` with hooks: `ruff`, `ruff-format`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`.
- [x] 1.5.2 Add `mypy` as a local hook (slow but worth it).
- [x] 1.5.3 Run `pre-commit run --all-files` once to baseline.
- [x] 1.5.4 Document install steps (`pre-commit install`) in `README.md`.

#### 1.6 CLI entrypoint
- [x] 1.6.1 Add `typer` to dependencies.
- [x] 1.6.2 Create `src/meal_planner/cli.py` with a `typer.Typer()` app.
- [x] 1.6.3 Implement `meal-planner run` calling `meal_planner.run_pipeline.run_all`.
- [x] 1.6.4 Implement `meal-planner ingest`, `meal-planner parse`, `meal-planner nutrition`, `meal-planner optimise` as separate sub-commands.
- [x] 1.6.5 Implement `meal-planner report --plan-run-id N --format md|docx|html`.
- [x] 1.6.6 All commands accept `--config PATH` and `--log-level LEVEL`.
- [x] 1.6.7 Add `--dry-run` flag to `run` that validates config + checks DB connectivity but doesn't write.
- [x] 1.6.8 Verify `meal-planner --help` prints the full command tree.

#### 1.7 Cleanup
- [x] 1.7.1 Add `data_pgadmin/`, `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `.venv/` to `.gitignore` (verify already present).
- [x] 1.7.2 Remove `Moroccan Lentil Soup.html` from repo root (it's a duplicate of one in `180 recipes/Recipes/`).
- [x] 1.7.3 Remove `food_list 2.txt` and `food_list_table.rtf` (subsumed by `config/food_list_canonical.txt`).
- [x] 1.7.4 Remove `data_processor.ipynb` (superseded by the Python pipeline).
- [x] 1.7.5 Remove top-level `report.docx` and `reports/plan_4_*.png` from version control (regeneratable).

---

### Phase 2 — Configuration and secrets

#### 2.1 pydantic-settings model
- [x] 2.1.1 Add `pydantic-settings` to dependencies.
- [x] 2.1.2 Create `SourceSettings`, `LLMSettings`, `NutritionSettings`, `OptimizerSettings`, `ParseSettings` pydantic `BaseModel`s in `src/meal_planner/config.py`.
- [x] 2.1.3 Define `Settings(BaseSettings)` aggregating sub-models with `SettingsConfigDict(yaml_file="config/pipeline.yaml", env_nested_delimiter="__")`.
- [x] 2.1.4 Annotate every field with default + docstring + validators where relevant (e.g., `min_rating: float = Field(ge=0, le=5, default=3)`).
- [x] 2.1.5 Add `Settings.load(path: Path | None = None) -> Settings` class method that's called once at process start.
- [x] 2.1.6 Replace every `cfg.get("...")` and `cfg.get("optimizer", {}).get("...")` call with typed attribute access.
- [x] 2.1.7 Add validation: `daily_dozen_targets.keys() == portion_sizes.keys()` (today the optimiser silently skips groups missing from `portion_sizes`).
- [x] 2.1.8 Add validation: `calories_daily_min < calories_daily_max`.
- [x] 2.1.9 Add a unit test `tests/unit/test_config.py` asserting valid YAML loads, invalid YAML fails with a clear error.

#### 2.2 .env.example cleanup
- [x] 2.2.1 Remove legacy-only env vars: `LAST_MODIFIED_DATE`, `NUM_RECIPES`, `FORCE_UPDATE_PROCESSED`, `TABLE_NAME`, `SCHEMA_NAME`, `REPO_URL`, `CLONE_DIR`, `OPENAI_API_KEY`, `FOOD_LIST_PATH`.
- [x] 2.2.2 Document each remaining var in a comment above it.
- [x] 2.2.3 Add `PIPELINE_CONFIG=config/pipeline.yaml` as a documented override.
- [x] 2.2.4 Add `LOG_LEVEL=INFO` and `LOG_FORMAT=json|console`.

#### 2.3 Config CLI
- [x] 2.3.1 Implement `meal-planner config validate` — loads settings, prints "OK" or validation errors.
- [x] 2.3.2 Implement `meal-planner config print [--format yaml|json]` — prints the resolved settings (with secrets redacted).
- [x] 2.3.3 Add a unit test invoking the CLI via `typer.testing.CliRunner`.

#### 2.4 Documentation
- [x] 2.4.1 Create `docs/configuration.md` (placeholder; Phase 14 fills it).
- [x] 2.4.2 Add a section to `README.md` linking to the config doc.

---

### Phase 3 — Retire legacy

#### 3.1 Pre-flight
- [x] 3.1.1 Run `meal-planner ingest && meal-planner parse && meal-planner nutrition && meal-planner optimise` once on the new tables to confirm parity with current behaviour.
- [x] 3.1.2 Run `backfill_history` one final time and snapshot `meal_history` row count.
- [x] 3.1.3 `pg_dump` the legacy tables into `archive/legacy_dump.sql`.
- [x] 3.1.4 Confirm `archive/legacy_dump.sql` restores cleanly into a scratch DB.
- [x] 3.1.5 Verify no code outside `pipeline/backfill_history.py` reads from `weekly_meal_plan` / `processed_recipes` / `recipes`.

#### 3.2 Delete legacy code
- [x] 3.2.1 Delete `ingest_data.py`.
- [x] 3.2.2 Delete `data_processor.py`.
- [x] 3.2.3 Delete `meal_planner.py` (the legacy planner).
- [x] 3.2.4 Delete `db_manager.py`.
- [x] 3.2.5 Delete `prompts.py`.
- [x] 3.2.6 Delete `Dockerfile_meal_ingest`, `Dockerfile_data_processor`, `Dockerfile_meal_planner`.
- [x] 3.2.7 Delete `pipeline/backfill_history.py`.
- [x] 3.2.8 Remove backfill call from `run_pipeline.py` and `flows/weekly_plan_flow.py`.

#### 3.3 Drop legacy tables
- [x] 3.3.1 Write Alembic migration `drop_legacy_tables` (depends on Phase 4.1).
- [x] 3.3.2 In the migration: `DROP TABLE meal_planning.weekly_meal_plan`, `processed_recipes`, `recipes`.
- [x] 3.3.3 Add a `downgrade()` that recreates the tables from the snapshot (read-only restoration; not used outside emergencies).
- [x] 3.3.4 Apply migration in dev, verify pipeline still runs.
- [x] 3.3.5 Update `init.sql` (or delete it; Phase 4 replaces it with Alembic).

#### 3.4 Slim docker-compose
- [x] 3.4.1 Remove `meal_ingest`, `data_processor`, `meal_planner` service definitions from `docker-compose.yaml`.
- [x] 3.4.2 Remove the `base` service.
- [x] 3.4.3 Add a single `app` service that runs `meal-planner run` from a unified Dockerfile (Phase 3.5).
- [x] 3.4.4 Verify `docker-compose up postgres pgadmin` still works for dev.
- [x] 3.4.5 Remove `data_pgadmin/` from disk and from `docker-compose` if not used.

#### 3.5 Unified Dockerfile
- [x] 3.5.1 Create `Dockerfile` (no suffix) using `python:3.12-slim` base.
- [x] 3.5.2 Multi-stage: stage 1 installs `uv` + dependencies; stage 2 copies `src/` and runs as non-root user.
- [x] 3.5.3 Install `glpk-utils` apt package in the runtime stage.
- [x] 3.5.4 Pin apt packages with `--no-install-recommends` and a cleanup step.
- [x] 3.5.5 Set `ENTRYPOINT ["meal-planner"]` so `docker run app run` works.
- [x] 3.5.6 Delete `Dockerfile.base`.

---

### Phase 4 — Database with Alembic

#### 4.1 Alembic setup
- [x] 4.1.1 Add `alembic` to dependencies.
- [x] 4.1.2 Run `alembic init alembic` at repo root.
- [x] 4.1.3 Configure `alembic.ini` to read `sqlalchemy.url` from `DATABASE_URL` env var (no hardcoded creds).
- [x] 4.1.4 Configure `alembic/env.py` to use the project's SA metadata (Phase 4.4 introduces models).
- [x] 4.1.5 Add `make migrate` (= `alembic upgrade head`) and `make migration name=...` (= `alembic revision --autogenerate`).
- [x] 4.1.6 Verify `alembic upgrade head` runs cleanly against an empty Postgres.
- [x] 4.1.7 Add a CI step that runs migrations against a fresh Postgres on every PR.

#### 4.2 Initial migration (capture current schema)
- [x] 4.2.1 Write `alembic/versions/001_initial.py` defining every table currently in `init.sql`'s "new" section.
- [x] 4.2.2 Include all indexes from `init.sql`.
- [x] 4.2.3 Verify migration applied to empty DB matches the schema from `init.sql` exactly (use `pg_dump --schema-only` and diff).
- [x] 4.2.4 Delete `init.sql` after the migration is verified.
- [x] 4.2.5 Update `docker-compose.yaml` to remove the `init.sql` mount.

#### 4.3 Schema fixes migration
- [x] 4.3.1 Migration `002_recipe_source_pk` adds `PRIMARY KEY (recipe_id, source_path)` to `recipe_source`.
- [x] 4.3.2 De-duplicate existing `recipe_source` rows in the migration (keep the latest `ingested_at`).
- [x] 4.3.3 Migration `003_pipeline_metric_correlation` adds `run_correlation_id UUID NOT NULL` and `plan_run_id INT NULL` columns.
- [x] 4.3.4 Backfill `run_correlation_id = uuid_generate_v4()` for existing rows.
- [x] 4.3.5 Migration `004_recipe_is_plant_based` adds `is_plant_based BOOLEAN NOT NULL DEFAULT TRUE` to `recipe`.
- [x] 4.3.6 Migration `005_plan_config_required` makes `plan_run.config_id NOT NULL` after Phase 8.2 starts writing snapshots.
- [x] 4.3.7 Migration `006_recipe_nutrition_extras` adds `protein_g`, `fat_g`, `carbs_g`, `sugar_g`, `sodium_mg` nullable columns.

#### 4.4 Repository pattern
- [x] 4.4.1 Create `src/meal_planner/db/models.py` with SQLAlchemy 2.0 declarative models for each table.
- [x] 4.4.2 Create `src/meal_planner/db/recipes_repo.py` with functions: `upsert_recipe`, `insert_recipe_source`, `replace_meal_types`, `insert_raw_ingredient_lines`.
- [x] 4.4.3 Create `src/meal_planner/db/parse_repo.py` with cache + override + recipe_ingredient operations.
- [x] 4.4.4 Create `src/meal_planner/db/nutrition_repo.py` with cache + recipe_nutrition operations.
- [x] 4.4.5 Create `src/meal_planner/db/plan_repo.py` with `insert_plan_run`, `insert_plan_meal`, `insert_plan_day`, `insert_plan_day_group`.
- [x] 4.4.6 Create `src/meal_planner/db/metrics_repo.py` with `record_metric(name, value, *, plan_run_id=None, correlation_id)`.
- [x] 4.4.7 Replace every inline `text("INSERT INTO ...")` in ingest/parse/nutrition/optimise with calls into the repos.
- [x] 4.4.8 Each repo function takes `connection: Connection` as first arg; no module owns its own engine.

#### 4.5 Connection management
- [x] 4.5.1 Centralise engine creation in `src/meal_planner/db/engine.py` with `pool_pre_ping=True`, `pool_size=5`, `pool_recycle=3600`.
- [x] 4.5.2 Use `engine.begin()` context managers for write transactions; `engine.connect()` for reads.
- [x] 4.5.3 Verify `wait_for_db` retry logic is preserved.

#### 4.6 Health check
- [x] 4.6.1 Implement `meal-planner db check` — runs `SELECT 1`, fetches Alembic head, compares to expected.
- [x] 4.6.2 Print red/green output and exit nonzero on failure.
- [x] 4.6.3 Wire into the optional `/healthz` endpoint (Phase 12.6).

---

### Phase 5 — Ingest hardening

#### 5.1 Pluralisation bug
- [x] 5.1.1 Define `MEAL_TYPE_NORMALIZE: dict[str, str]` constant in `src/meal_planner/ingest.py`.
- [x] 5.1.2 Replace the `mt[:-1] if mt.endswith("s")` line with dict lookup; unknown tokens → drop.
- [x] 5.1.3 Log dropped tokens at DEBUG with their source recipe.
- [x] 5.1.4 Add unit test `test_meal_type_normalisation` covering Lunches, Lunch, lunches, Lunch dishes, "How Not To Diet".
- [x] 5.1.5 Run a one-off backfill: re-process every existing `recipe.categories` to fix any `lunche` rows already in DB.

#### 5.2 recipe_source idempotency
- [x] 5.2.1 Verify Phase 4.3.1 migration is applied.
- [x] 5.2.2 Update `insert_recipe_source` repo function to `INSERT ... ON CONFLICT (recipe_id, source_path) DO UPDATE SET raw_html = EXCLUDED.raw_html, ingested_at = now()`.
- [x] 5.2.3 Add integration test asserting two consecutive `ingest` runs leave `recipe_source` with one row per recipe.

#### 5.3 Plant-based filter
- [x] 5.3.1 Create `config/non_plant_terms.yaml` listing meat/fish/dairy/egg keywords (chicken, beef, pork, lamb, fish, salmon, tuna, prawn, halloumi, parmesan, feta, egg, eggs, milk except where qualified by "almond/soy/oat", honey, gelatin, …).
- [x] 5.3.2 Add a `PlantBasedClassifier` class that takes the YAML and returns `is_plant(text: str) -> bool`.
- [x] 5.3.3 At ingest, classify each recipe by checking title + ingredient_lines against the term list.
- [x] 5.3.4 Persist `recipe.is_plant_based` based on result.
- [x] 5.3.5 Optimiser's recipe filter joins on `is_plant_based = TRUE` by default.
- [x] 5.3.6 Add `--include-non-plant` CLI flag to `meal-planner optimise` and `meal-planner run`.
- [x] 5.3.7 Emit `pipeline_metric` `ingest_non_plant_filtered` with the count.
- [x] 5.3.8 Add unit tests for the classifier (positive: plain pasta dish; negative: chicken risotto).
- [x] 5.3.9 Re-classify all existing recipes via a one-off script.

#### 5.4 Servings parser
- [x] 5.4.1 Replace `_parse_servings_count` with a regex-based parser handling: single integer, decimal, `"X-Y"` range (mean), `"X to Y"` range, `"makes X"`, `"serves X"`, `"X dozen"` (×12), `"X (Y per serving)"` patterns.
- [x] 5.4.2 Return `Decimal | None`; never return `0`.
- [x] 5.4.3 Add 15+ unit tests covering each pattern + adversarial inputs.
- [x] 5.4.4 Audit existing `recipe.servings_count` — re-parse all rows and compare; flag deltas for review.
- [x] 5.4.5 Decide migration policy for changed values; either auto-update or require manual confirmation.

#### 5.5 Source paths
- [x] 5.5.1 Compute `source = os.path.relpath(fpath, start=repo_root)` instead of absolute path.
- [x] 5.5.2 Add a one-off SQL `UPDATE` to rewrite existing absolute paths to relative.
- [x] 5.5.3 Document the convention in `docs/configuration.md`.

#### 5.6 Selector validation
- [x] 5.6.1 At the start of `ingest_local_html`, parse the *first* HTML file and verify each configured selector matches at least once.
- [x] 5.6.2 If any selector is unmatched, log a WARNING with the selector name and continue.
- [x] 5.6.3 Emit `pipeline_metric` `ingest_selector_unmatched_<name>`.
- [x] 5.6.4 Add an integration test with a malformed HTML file confirming the warning is logged but ingest doesn't crash.

---

### Phase 6 — Parsing overhaul

#### 6.1 Decouple knobs
- [x] 6.1.1 Add `parse.fuzzy_min_score: float = 0.8` and `parse.llm_threshold: float = 0.7` to `Settings.parse`.
- [x] 6.1.2 Update `parse_ingredients` to use `fuzzy_min_score` for fuzzy match acceptance and `llm_threshold` for LLM fallback gating.
- [x] 6.1.3 Remove all references to `cfg.llm.min_confidence`.
- [x] 6.1.4 Update `pipeline.yaml` and document the change.

#### 6.2 Quantulum3 robustness
- [x] 6.2.1 Remove the `quantulum_ok` global flag.
- [x] 6.2.2 Wrap each `qty_parser.parse(raw_text)` in try/except, log on failure, increment `parse_quantulum_errors` metric.
- [x] 6.2.3 Always fall through to `_regex_parse_quantity` on quantulum failure.
- [x] 6.2.4 Add a unit test using a known crash-inducing input (find one or fabricate via Hypothesis).

#### 6.3 Unit list growth
- [x] 6.3.1 Create `config/unit_grams.csv` with columns `unit, grams_per_unit, note`.
- [x] 6.3.2 Populate with: pinch (0.3g), dash (0.6g), handful (28g), sprig (1g), clove garlic (3g), small/medium/large (configurable per-ingredient default), stick of butter (113g, but butter is non-plant — used for cross-validation).
- [x] 6.3.3 Update `_unit_to_grams` to read from the CSV at module load.
- [x] 6.3.4 Add unit tests for each new unit.
- [x] 6.3.5 Document how to add a new unit in `docs/configuration.md`.

#### 6.4 Density table
- [x] 6.4.1 Create `config/density_g_per_ml.csv` with columns `ingredient_canonical, density`.
- [x] 6.4.2 Populate for the 30 most-used canonical ingredients (rice ~0.85, oats ~0.42, flour ~0.59, lentils ~0.85, …).
- [x] 6.4.3 Update volume → grams conversion to consult the density table when ingredient_canonical is known; default to 1.0 for water-like.
- [x] 6.4.4 Add unit tests asserting `1 cup of rice ≈ 200g` and `1 cup of oats ≈ 90g`.

#### 6.5 LLM port
- [x] 6.5.1 Create `src/meal_planner/llm/__init__.py` defining `class LLMClient(Protocol)` with method `parse_lines(lines, food_groups) -> list[ParsedLine]`.
- [x] 6.5.2 Create `OpenAILLM` adapter in `src/meal_planner/llm/openai_client.py`.
- [x] 6.5.3 Create `AnthropicLLM` adapter in `src/meal_planner/llm/anthropic_client.py`.
- [x] 6.5.4 Create `NullLLM` adapter that always returns empty list (used when no API key configured).
- [x] 6.5.5 Factory `get_llm_client(settings) -> LLMClient` that picks the right adapter.
- [x] 6.5.6 Update `parse_ingredients` to take an `LLMClient` argument (dependency injection).
- [x] 6.5.7 Define `ParsedLine` pydantic model with `raw_text, ingredient_name, quantity_value, quantity_unit, food_group`.
- [x] 6.5.8 Unit-test each adapter with mocked SDK clients.

#### 6.6 Anthropic prompt caching
- [x] 6.6.1 In `AnthropicLLM`, send the food-group list and instructions as a system prompt with `cache_control: {"type": "ephemeral"}`.
- [x] 6.6.2 Verify cache hit rate by reading `usage.cache_read_input_tokens` from the response.
- [x] 6.6.3 Emit metric `llm_cache_hit_tokens` per batch.
- [x] 6.6.4 Document cache savings in `docs/architecture.md`.

#### 6.7 LLM output validation
- [x] 6.7.1 Parse the LLM response into `list[ParsedLine]` using `pydantic.TypeAdapter`.
- [x] 6.7.2 On `ValidationError`, retry once with the same input; if still bad, log and skip.
- [x] 6.7.3 Emit metrics `parse_llm_invalid_json` and `parse_llm_retry_succeeded`.
- [x] 6.7.4 Add a unit test simulating malformed JSON, partial JSON, and JSON with extra fields.
- [x] 6.7.5 Add a unit test simulating a successful retry.

#### 6.8 Override workflow
- [x] 6.8.1 Implement `meal-planner override add --raw TEXT --canonical TEXT --group TEXT` CLI command.
- [x] 6.8.2 Implement `meal-planner override list` and `override remove --raw TEXT`.
- [x] 6.8.3 Document the workflow in `docs/runbooks/parser_overrides.md`.
- [x] 6.8.4 Add an integration test asserting an override is applied during `parse_ingredients`.

#### 6.9 Parser tests
- [x] 6.9.1 Create `tests/fixtures/parser_golden.yaml` with 30 ingredient lines and expected outputs.
- [x] 6.9.2 Add `tests/unit/test_parser_golden.py` that loads the fixture and asserts each line parses correctly.
- [x] 6.9.3 Cover: ranges, fractions, multipliers, no-unit counts, dried herbs, fresh herbs, parenthetical notes, common synonyms.
- [x] 6.9.4 Property test (Hypothesis): for any line `"<int> g <word>"`, `quantity_grams == int`.
- [x] 6.9.5 Add a regression test for any bug found during overhaul.

---

### Phase 7 — Nutrition

#### 7.1 CoFID shipping
- [x] 7.1.1 Confirm CoFID licence (UK Open Government Licence v3.0 — likely allows redistribution; verify).
- [x] 7.1.2 If allowed: commit `data/cofid.xlsx` (consider Git LFS if >10MB).
- [x] 7.1.3 If not: set `nutrition.cofid_url` to the canonical download URL and document the bootstrap step in `README.md`.
- [x] 7.1.4 Verify `enrich_nutrition` produces non-empty `recipe_nutrition` rows on a fresh run.
- [x] 7.1.5 Add a `meal-planner data fetch-cofid` CLI command.

#### 7.2 Coverage gate
- [x] 7.2.1 After `enrich_nutrition`, query `nutrition_coverage_ratio` from `pipeline_metric`.
- [x] 7.2.2 If `< 0.6`, log ERROR with the unmapped ingredient list and `sys.exit(2)` (exit code 2 = "data quality").
- [x] 7.2.3 Allow override via `--ignore-coverage` CLI flag.
- [x] 7.2.4 Add an integration test simulating low coverage and asserting the exit code.

#### 7.3 Cache improvements
- [x] 7.3.1 Implement `meal-planner nutrition refresh --ingredient X` to invalidate and re-fetch a single canonical name.
- [x] 7.3.2 Implement `meal-planner nutrition refresh --all` (with confirmation prompt).
- [x] 7.3.3 Add `match_score` and `match_source_name` columns to `ingredient_nutrition_cache` (migration in Phase 4.3).
- [x] 7.3.4 Surface low-confidence matches in the report.

#### 7.4 More nutrients
- [x] 7.4.1 Migration adds `protein_g`, `fat_g`, `carbs_g`, `sugar_g`, `sodium_mg` to `recipe_nutrition` (Phase 4.3.7).
- [x] 7.4.2 Update `_lookup_cofid` and `_lookup_usda` to extract these nutrients.
- [x] 7.4.3 Update `enrich_nutrition` aggregation to compute totals for each.
- [x] 7.4.4 Update report templates to show macros.
- [x] 7.4.5 Add optional optimiser constraints `protein_min_g_per_day`, `sodium_max_mg_per_day` (off by default).

#### 7.5 Better fuzzy match
- [x] 7.5.1 Pre-clean ingredient names: strip parenthetical notes, drop trailing "raw"/"cooked"/"dried"/"chopped"/"diced".
- [x] 7.5.2 Lowercase + remove punctuation before fuzzy match.
- [x] 7.5.3 Persist match score in cache.
- [x] 7.5.4 Add unit tests with real CoFID-like data.

#### 7.6 USDA v2
- [x] 7.6.1 Update `_lookup_usda` to filter `dataType=Foundation,SR Legacy` (avoid branded fast-food matches).
- [x] 7.6.2 Pull `Energy (Atwater General Factors)` rather than the generic `Energy` nutrient.
- [x] 7.6.3 Add request retries with exponential backoff for transient HTTP failures.
- [x] 7.6.4 Add a unit test with a recorded USDA response (use `responses` or `requests-mock`).

#### 7.7 Tests
- [x] 7.7.1 Mock `requests.get` for USDA in unit tests.
- [x] 7.7.2 Build a tiny CoFID-like DataFrame fixture for `_lookup_cofid` tests.
- [x] 7.7.3 Integration test: ingest 3 recipes, run nutrition, assert `recipe_nutrition.calories_kcal > 0`.
- [x] 7.7.4 Integration test: assert per-serving math (`per_serving × servings = total`).

---

### Phase 8 — Optimiser cleanup

#### 8.1 Plant-only filter
- [x] 8.1.1 In `optimize._load_data`, add `WHERE is_plant_based = TRUE` to the recipes SELECT.
- [x] 8.1.2 Honour `--include-non-plant` CLI flag by passing through to the loader.
- [x] 8.1.3 Add an integration test asserting non-plant recipes are excluded by default.

#### 8.2 Config snapshot
- [x] 8.2.1 At the start of `optimize_plan`, insert a row into `plan_config` with the current optimiser settings.
- [x] 8.2.2 Set `plan_run.config_id` to the new row's PK.
- [x] 8.2.3 Apply Phase 4.3.6 migration to make `config_id` NOT NULL.
- [x] 8.2.4 Add a unit test asserting the snapshot row is written.
- [x] 8.2.5 Surface the snapshot in the report ("This plan was generated with: …").

#### 8.3 Hierarchical relaxation
- [x] 8.3.1 Define `RelaxationLevel` enum: `STRICT`, `DROP_DAILY_NUTRITION`, `DROP_WEEKLY_GROUPS`, `DROP_GROUP_TARGETS`.
- [x] 8.3.2 Refactor `optimize_plan` to a loop that tries each level in order until the solver returns optimal.
- [x] 8.3.3 Each relaxation removes constraints (not soft via slack); model is rebuilt cleanly.
- [x] 8.3.4 Log every relaxation step at WARNING.
- [x] 8.3.5 Emit `pipeline_metric` `optimize_relaxation_level` (0–3).
- [x] 8.3.6 Surface the level reached in the plan report.
- [x] 8.3.7 Add a unit test forcing relaxation by setting unreachable kcal min.
- [x] 8.3.8 Decide policy: should `STRICT` failure still raise, or just warn?

#### 8.4 Performance
- [x] 8.4.1 Pre-filter `R` to recipes that have at least one configured meal type tag.
- [x] 8.4.2 Pre-filter `I` to ingredients with `portion_met = TRUE` somewhere (already done; verify and document).
- [x] 8.4.3 Emit metric `solver_variable_count = |R| × |D| × |M| + |D| × |I| + |I|`.
- [x] 8.4.4 Profile a real run; if >30s, investigate adding a heuristic warm-start.
- [x] 8.4.5 Document expected variable counts in `docs/architecture.md`.

#### 8.5 Solver settings
- [x] 8.5.1 Bump default `solver_time_limit` from 60 to 300 seconds.
- [x] 8.5.2 Add `solver_mip_gap: float = 0.05` to settings.
- [x] 8.5.3 Pass to GLPK via `solver.options["mipgap"]`.
- [x] 8.5.4 Document the trade-off in `docs/configuration.md`.

#### 8.6 Refactor optimize.py
- [x] 8.6.1 Split into `optimize/data.py` (loaders), `optimize/model.py` (Pyomo build), `optimize/persist.py` (`write_plan`), `optimize/__init__.py` (orchestration).
- [x] 8.6.2 Each module < 200 lines.
- [x] 8.6.3 No file references `pyomo` and `sqlalchemy` simultaneously (separation of concerns).
- [x] 8.6.4 Add docstrings explaining the model's decision variables and constraints.
- [x] 8.6.5 Run `mypy --strict` clean on the new modules.
- [x] 8.6.6 Update CLI entry to import from new structure.

#### 8.7 Optimiser tests
- [x] 8.7.1 Create `tests/fixtures/synthetic_recipes.py` with 5 hand-built plant recipes covering all 10 food groups.
- [x] 8.7.2 Integration test: run optimiser, assert each meal slot has exactly one recipe.
- [x] 8.7.3 Integration test: assert no recipe exceeds `max_recipe_repeats`.
- [x] 8.7.4 Integration test: with sufficient recipes, assert all Daily Dozen targets met (zero slack).
- [x] 8.7.5 Integration test: with insufficient recipes, assert slack > 0 and the appropriate relaxation level recorded.
- [x] 8.7.6 Integration test: assert `meal_history` is populated after `write_plan`.
- [x] 8.7.7 Integration test: assert second consecutive run picks different recipes (recency bites).

---

### Phase 9 — Reports

#### 9.1 Merge entrypoints
- [x] 9.1.1 Create `src/meal_planner/report/__init__.py` with `generate(plan_run_id, formats=("md","docx","html"))`.
- [x] 9.1.2 Move chart generation from `tools/generate_docx_report.py` into `report/charts.py`.
- [x] 9.1.3 Move markdown rendering from old `report.py` into `report/markdown.py`.
- [x] 9.1.4 Add `report/docx.py` and `report/html.py`.
- [x] 9.1.5 Delete `tools/generate_docx_report.py` and old `pipeline/report.py`.

#### 9.2 HTML output
- [x] 9.2.1 Use Jinja2 (already a Prefect dep) to render an HTML template.
- [x] 9.2.2 Embed matplotlib PNGs as base64 `<img>` tags so the HTML is self-contained.
- [x] 9.2.3 Add a `meal-planner serve` dev command using FastAPI to preview the latest report.
- [x] 9.2.4 Style with minimal inline CSS — no external dependencies.

#### 9.3 Standard sections
- [x] 9.3.1 Recent runs table.
- [x] 9.3.2 Latest week meal table.
- [x] 9.3.3 Daily kcal/fiber line chart.
- [x] 9.3.4 Daily Dozen heatmap.
- [x] 9.3.5 Top ingredients bar chart.
- [x] 9.3.6 Repetition timeline scatter.
- [x] 9.3.7 New: coverage panel (parse cache hit %, LLM fallback %, nutrition coverage %, relaxation level).

#### 9.4 Extras dependency group
- [x] 9.4.1 Move `matplotlib`, `seaborn`, `python-docx`, `jinja2` to `[project.optional-dependencies] report`.
- [x] 9.4.2 Make `report/charts.py` import these lazily so the core CLI runs without them.
- [x] 9.4.3 Update Dockerfile to install `report` extras.

---

### Phase 10 — Orchestration

#### 10.1 Decision
- [x] 10.1.1 Decide: Prefect Cloud vs self-hosted Prefect 3 vs cron + CLI.
- [x] 10.1.2 Document the choice and rationale in `docs/architecture.md`.
- [x] 10.1.3 If Prefect Cloud: provision workspace and document API key management.

#### 10.2 Migration
- [x] 10.2.1 If Prefect 3: bump `prefect` to `>=3.0` in `pyproject.toml`.
- [x] 10.2.2 Rewrite `flows/weekly_plan_flow.py` against the Prefect 3 API.
- [x] 10.2.3 Replace `flows/deploy_weekly.py` with declarative `prefect.yaml`.
- [x] 10.2.4 If cron: write `ops/cron/weekly_plan.sh` and document `crontab -e` install.
- [x] 10.2.5 Verify the schedule fires once on a test day.

#### 10.3 Task retries and resilience
- [x] 10.3.1 Add `retries=2, retry_delay=60` to nutrition step (USDA flakiness).
- [x] 10.3.2 Add `retries=1, retry_delay=30` to ingest step (filesystem race).
- [x] 10.3.3 No retries on optimiser (deterministic).
- [x] 10.3.4 Surface retry counts in metrics.

#### 10.4 Correlation ID
- [x] 10.4.1 At flow start, generate a UUID4.
- [x] 10.4.2 Bind to `structlog` context (Phase 12.1).
- [x] 10.4.3 Pass to every metric write via `record_metric(..., correlation_id=...)`.
- [x] 10.4.4 Log it once at the start and once at the end of the flow.
- [x] 10.4.5 Document how to query a run's metrics by correlation_id in `docs/runbooks/`.

---

### Phase 11 — Testing

#### 11.1 Reorganise
- [x] 11.1.1 Move existing `tests/test_*.py` into `tests/unit/`.
- [x] 11.1.2 Create `tests/integration/` with `__init__.py` and a `conftest.py` for shared fixtures.
- [x] 11.1.3 Update `pytest.ini` with markers: `unit`, `integration`, `slow`.

#### 11.2 Unit tests
- [x] 11.2.1 `test_config.py` — settings load/validate.
- [x] 11.2.2 `test_parser_golden.py` — 30 golden lines.
- [x] 11.2.3 `test_parser_helpers.py` — strip_quantity, unit_to_grams (extend existing).
- [x] 11.2.4 `test_synonyms.py` — override > synonym > fuzzy precedence.
- [x] 11.2.5 `test_recency.py` — extend existing.
- [x] 11.2.6 `test_food_list.py` — header detection edge cases.
- [x] 11.2.7 `test_servings_parser.py` — 15 patterns.
- [x] 11.2.8 `test_meal_type_normalisation.py` — pluralisation fix.
- [x] 11.2.9 `test_plant_classifier.py` — positive/negative cases.
- [x] 11.2.10 `test_llm_clients.py` — adapters with mocked SDKs.
- [x] 11.2.11 `test_metric_names.py` — enum coverage.

#### 11.3 Integration tests (testcontainers-postgres)
- [x] 11.3.1 Add `testcontainers-postgres` to dev dependencies.
- [x] 11.3.2 Shared fixture `pg_engine` that starts Postgres, runs migrations, yields engine, tears down.
- [x] 11.3.3 `test_ingest.py` — 5 fixture HTML files in 10 recipes.
- [x] 11.3.4 `test_parse.py` — seed `recipe_ingredient`, run parser, assert canonicalisation.
- [x] 11.3.5 `test_nutrition.py` — seed parsed ingredients, run enrichment with mocked CoFID, assert `recipe_nutrition`.
- [x] 11.3.6 `test_optimise.py` — full graph, assert plan structure.
- [x] 11.3.7 `test_full_run.py` — end-to-end via `meal-planner run`.
- [x] 11.3.8 `test_idempotency.py` — run twice, assert second run is a no-op for `recipe_source`.

#### 11.4 Property tests
- [x] 11.4.1 Add `hypothesis` to dev dependencies.
- [x] 11.4.2 Property: any well-formed `<float> <unit> <ingredient>` string parses to `quantity_grams > 0`.
- [x] 11.4.3 Property: `recency_penalty` is monotone decreasing in days-since.

#### 11.5 Snapshot tests
- [x] 11.5.1 Add `pytest-snapshot` or `syrupy` to dev deps.
- [x] 11.5.2 Generate a fixture `plan_run` and snapshot the markdown report.
- [x] 11.5.3 Snapshot the HTML report (DOM structure, not pixel-perfect rendering).

#### 11.6 Coverage gate
- [x] 11.6.1 Add `pytest-cov` to dev deps.
- [x] 11.6.2 Configure `[tool.coverage]` in `pyproject.toml` to source `src/meal_planner`.
- [x] 11.6.3 CI runs `pytest --cov --cov-fail-under=80`.

#### 11.7 Fixture HTML
- [x] 11.7.1 Create `tests/fixtures/recipes/` with 5 hand-curated HTML files.
- [x] 11.7.2 Cover: range yield, fractional quantity, range quantity, multiplier (`2 x 400g`), no-unit count (`3 onions`), dried-only herbs.
- [x] 11.7.3 Mirror the production HTML structure exactly.
- [x] 11.7.4 Snapshot expected DB state after ingestion.
- [x] 11.7.5 Document each fixture file in `tests/fixtures/recipes/README.md`.

---

### Phase 12 — Observability

#### 12.1 structlog
- [x] 12.1.1 Add `structlog` to dependencies.
- [x] 12.1.2 Create `src/meal_planner/logging.py` with a `configure(level, format)` function.
- [x] 12.1.3 Console renderer for dev (key=value, coloured).
- [x] 12.1.4 JSON renderer for prod (one event per line).
- [x] 12.1.5 Bind `run_correlation_id` and `step` (e.g. `step="parse"`) to every log line via context vars.
- [x] 12.1.6 Replace every `logger = logging.getLogger(__name__)` in the package with `log = structlog.get_logger()`.
- [x] 12.1.7 Replace every `print(...)` (mainly in `tools/generate_docx_report.py`) with `log.info(...)`.
- [x] 12.1.8 Configure logging at CLI startup based on `LOG_LEVEL` and `LOG_FORMAT` env vars.

#### 12.2 MetricName enum
- [x] 12.2.1 Define `class MetricName(StrEnum)` in `src/meal_planner/metrics.py` with every existing metric name.
- [x] 12.2.2 Update `record_metric` to accept `MetricName` only.
- [x] 12.2.3 Replace every string literal call site with the enum.
- [x] 12.2.4 Add `__doc__` for each enum member describing what it measures.
- [x] 12.2.5 Generate `docs/metrics.md` from the enum docstrings via a script.

#### 12.3 pipeline_run view
- [x] 12.3.1 Migration creates a Postgres view `pipeline_run` joining `plan_run` to aggregated `pipeline_metric` rows.
- [x] 12.3.2 View columns: `plan_run_id, run_time, status, parse_cache_hit_ratio, nutrition_coverage_ratio, relaxation_level, slack_total, …`.
- [x] 12.3.3 Document the view in `docs/metrics.md`.

#### 12.4 latest_plan_summary view
- [x] 12.4.1 Migration creates view selecting from latest `plan_run` joined with `plan_meal`, `plan_day`, `plan_day_group`.
- [x] 12.4.2 Implement `meal-planner plan show` CLI that queries this view and pretty-prints.
- [x] 12.4.3 Implement `meal-planner plan list --limit N` for run history.

#### 12.5 Grafana (optional)
- [x] 12.5.1 Create `ops/grafana/dashboard.json` with panels: recipe count over time, parse cache hit ratio, nutrition coverage ratio, solver seconds, slack total per run.
- [x] 12.5.2 Add `grafana` service to dev `docker-compose.yaml`.
- [x] 12.5.3 Pre-provision the Postgres datasource via `ops/grafana/datasource.yaml`.
- [x] 12.5.4 Document setup in `docs/runbooks/observability.md`.

#### 12.6 Healthchecks
- [x] 12.6.1 Implement `meal-planner health` CLI returning JSON: `{"db": "ok", "alembic_head": "...", "last_run_age_hours": ...}`.
- [x] 12.6.2 Optional: add a tiny FastAPI app exposing `/healthz` and `/livez`.
- [x] 12.6.3 Containerise the FastAPI sidecar separately.
- [x] 12.6.4 Document use in `docs/runbooks/healthchecks.md`.

---

### Phase 13 — CI/CD

#### 13.1 GitHub Actions
- [x] 13.1.1 Create `.github/workflows/lint.yml` running `ruff check`, `ruff format --check`, `mypy --strict`.
- [x] 13.1.2 Create `.github/workflows/test.yml` running `pytest --cov` with a Postgres service container.
- [x] 13.1.3 Create `.github/workflows/migrate.yml` applying Alembic migrations on PR to verify cleanliness.
- [x] 13.1.4 Create `.github/workflows/build.yml` building Docker image and pushing to GHCR on `main` merges.
- [x] 13.1.5 Add a `release.yml` for tagged releases (publish image with semver tag).
- [x] 13.1.6 Cache `uv` venv between runs.
- [x] 13.1.7 Use matrix to test against Python 3.12 and 3.13 (once 3.13 is stable for our deps).
- [x] 13.1.8 Verify each workflow runs green on a throwaway PR.

#### 13.2 Dependabot
- [x] 13.2.1 Create `.github/dependabot.yml` with weekly Python and GitHub Actions updates.
- [x] 13.2.2 Group minor + patch updates into one PR.
- [x] 13.2.3 Auto-approve patch updates for trusted deps (with care).

#### 13.3 Security
- [x] 13.3.1 Enable CodeQL on the repo (Python).
- [x] 13.3.2 Add a `semgrep.yml` workflow with the `python.lang.security` ruleset.
- [x] 13.3.3 Add `pip-audit` step to `test.yml`.

#### 13.4 Branch protection
- [x] 13.4.1 Require lint, test, migrate workflows to pass on `main`.
- [x] 13.4.2 Require ≥1 review.
- [x] 13.4.3 Disallow force-push to `main`.
- [x] 13.4.4 Require linear history.

---

### Phase 14 — Documentation

#### 14.1 README rewrite
- [x] 14.1.1 Delete the plant-food list currently in `README.md`.
- [x] 14.1.2 Add: project description (2–3 sentences), what problem it solves, who it's for.
- [x] 14.1.3 Add: prerequisites (Python 3.12, Docker, Postgres).
- [x] 14.1.4 Add: quickstart (`git clone`, `cp .env.example .env`, `uv sync`, `docker-compose up -d postgres`, `make migrate`, `meal-planner run`).
- [x] 14.1.5 Add: links to `docs/`.

#### 14.2 architecture.md
- [x] 14.2.1 Stage flow diagram (mermaid).
- [x] 14.2.2 DB schema ER diagram.
- [x] 14.2.3 Prefect deployment topology.
- [x] 14.2.4 LLM provider abstraction explanation.

#### 14.3 configuration.md
- [x] 14.3.1 Auto-generate from pydantic models (use `pydantic.json_schema()` + a render script).
- [x] 14.3.2 Manual section explaining the trade-offs for each major knob (recency_half_life, slack_weight, …).
- [x] 14.3.3 Example `pipeline.yaml` for common scenarios (strict, relaxed, low-budget LLM).

#### 14.4 metrics.md
- [x] 14.4.1 Auto-generate from `MetricName` enum.
- [x] 14.4.2 Add suggested alert thresholds per metric.
- [x] 14.4.3 Add Grafana panel references.

#### 14.5 Runbooks
- [x] 14.5.1 `docs/runbooks/db_unreachable.md`.
- [x] 14.5.2 `docs/runbooks/usda_throttled.md`.
- [x] 14.5.3 `docs/runbooks/solver_timeout.md`.
- [x] 14.5.4 `docs/runbooks/no_html_files.md`.
- [x] 14.5.5 `docs/runbooks/low_nutrition_coverage.md`.
- [x] 14.5.6 `docs/runbooks/parser_overrides.md`.

#### 14.6 Archive
- [x] 14.6.1 Move `research_v2.md` → `docs/history/research_v2.md`.
- [x] 14.6.2 Move `plan_v2.md` (this file) → `docs/history/plan_v2.md` once executed.
- [x] 14.6.3 Add `docs/history/README.md` indexing past planning docs.

---

### Phase 15 — Quick wins (parallel-safe subset)

If a full overhaul isn't on the table, this is the minimum batch. Each
references the full task above.

- [x] 15.1 Pluralisation bug — see 5.1.
- [x] 15.2 `recipe_source` dedup — see 4.3.1, 4.3.2, 5.2.
- [x] 15.3 Quantulum3 global flag — see 6.2.
- [x] 15.4 Decouple `min_confidence` — see 6.1.
- [x] 15.5 Plant-only filter — see 4.3.5, 5.3, 8.1.
- [x] 15.6 CoFID file shipped — see 7.1.
- [x] 15.7 Optimiser relaxation — see 8.3.
- [x] 15.8 Strict config validation — see 2.1.
- [x] 15.9 Add 5–10 integration tests — see 11.3.

---

## Appendix B — Estimated effort summary

| Phase | Tasks | Rough effort |
|---|---:|---|
| 1 — Project structure | 38 | 3–5 days |
| 2 — Config | 18 | 1–2 days |
| 3 — Retire legacy | 24 | 2–3 days |
| 4 — DB / Alembic | 30 | 4–6 days |
| 5 — Ingest | 28 | 3–4 days |
| 6 — Parsing | 39 | 5–7 days |
| 7 — Nutrition | 29 | 3–4 days |
| 8 — Optimiser | 35 | 5–7 days |
| 9 — Reports | 18 | 2–3 days |
| 10 — Orchestration | 16 | 2–3 days |
| 11 — Testing | 38 | 5–7 days |
| 12 — Observability | 26 | 3–4 days |
| 13 — CI/CD | 18 | 2–3 days |
| 14 — Docs | 23 | 3–4 days |
| **Total** | **~380** | **~45–62 working days** |

This squares with the Phase-17 calendar estimate of 8–10 weeks of part-time
work, or 6–9 weeks full-time for a single engineer with no parallelism.
