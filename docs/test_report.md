# End-to-end test report (2026-05-05)

This report captures an actual execution of the rebuilt pipeline against a
local Postgres 14 (Homebrew) and the 180-recipe corpus in `recipes_html/`.
It is intended as a permanent record of what was verified, what failed, and
what was fixed in response.

## Test environment

- Postgres 14.15 (Homebrew, listening on `localhost:5432`)
- Python 3.13.1 in `.venv`
- 180 HTML recipes in `recipes_html/Recipes/`
- No CoFID file shipped, no USDA API key, no LLM API key
- Test database: `meal_planning_test`

## Tests executed

| # | Test | Result |
|---|---|---|
| 1 | `alembic upgrade head` against real Postgres | **fixed** after one bug |
| 2 | Schema verification (16 tables, 2 views) | passed |
| 3 | `meal-planner --help` | passed |
| 4 | `meal-planner config validate` and `config print` | passed |
| 5 | `meal-planner db check` against real DB | passed |
| 6 | `meal-planner override add/list` | passed |
| 7 | `meal-planner ingest` (180 recipes) | passed |
| 8 | `meal-planner parse` (2,121 ingredient lines) | passed |
| 9 | `meal-planner optimise` | **fixed** after two bugs |
| 10 | `meal-planner report --plan-run-id N --format md,html` | passed |
| 11 | `meal-planner run` end-to-end on a fresh DB | passed |
| 12 | `meal-planner plan list` and `plan show` | passed |
| 13 | `meal-planner run --dry-run` | passed |
| 14 | `meal-planner nutrition refresh` | passed |
| 15 | Plant-only filter on real plan output | passed |

## Issues found and resolved

### Issue 1 — Alembic env.py typo ([alembic/env.py](alembic/env.py))
Symptom: `AttributeError: 'Connection' object has no attribute 'execute_options'. Did you mean: 'execution_options'?`
Cause: Wrote `connection.execute_options(...)` instead of `connection.execution_options(...)`. Also missing an explicit `connection.commit()` after the `CREATE SCHEMA` since the connection was running outside of `begin()`.
Fix: Renamed call and added the commit. Verified by running `alembic upgrade head` against a fresh database — all three migrations apply cleanly.

### Issue 2 — Optimiser infeasible when no snack-tagged recipes exist ([src/meal_planner/optimize/run.py](src/meal_planner/optimize/run.py))
Symptom: `RuntimeError: all relaxation levels failed: infeasible` on the real corpus.
Cause: The 180-recipe corpus has 0 recipes tagged for the `snack` meal type, but the `meal_slot` constraint requires exactly one recipe per (day, meal-type) — no slack to absorb the missing snack recipe. The relaxation hierarchy did not relax this constraint either, so every level was infeasible.
Fix: Added `_maybe_force_snack_optional` which detects empty snack-tagged sets and flips `optimizer.snack_optional=True` automatically, with a warning log. The user keeps the option to set it explicitly. Verified the optimiser then solves at relaxation level 0.

### Issue 3 — Optimiser rejected `TerminationCondition.feasible` ([src/meal_planner/optimize/run.py](src/meal_planner/optimize/run.py))
Symptom: First two relaxation attempts logged `optimize.infeasible condition=feasible` despite GLPK actually returning a feasible solution.
Cause: The acceptance check was `if condition == TerminationCondition.optimal:` — a single equality. Pyomo's `TerminationCondition` enum has several "ok" outcomes: `optimal`, `feasible` (solver stopped before proving optimality, often because of MIP gap), `locallyOptimal`, `globallyOptimal`. `feasible` is what GLPK returns when it accepts a within-gap solution.
Fix: Replaced the equality with a set membership check across all four "ok" conditions. After the fix, the strict-level run on the corpus returns `feasible` (within mip gap) and is accepted as a valid solution.

### Issue 4 — `DB_HOST` default unfriendly to local development ([src/meal_planner/db/engine.py](src/meal_planner/db/engine.py), [.env.example](.env.example))
Symptom: `meal-planner db check` with no env vars set tries to connect to host `postgres` (the docker-compose service name) and fails locally.
Cause: The defaults in `build_url` are tuned for the docker-compose stack, which is correct for that deployment but unhelpful when a developer is running everything on bare metal against a local Postgres.
Fix recommended (not applied to avoid regressing the docker workflow): Document in the README and the `.env.example` that local development requires `DB_HOST=localhost`. Long term, consider auto-detecting (e.g., fall back to `localhost` if `postgres` doesn't resolve), but that's outside the immediate test scope.

## Numbers from the real run

```
files=180 recipes=180 ingredients=2121 non_plant=83
parse_total=2121 cached=0 llm_used=0 invalid_json=0
nutrition_total=726 covered=0 coverage=0.0  (no CoFID, no USDA key)
plant-based recipes filtered to rating>=3: 73
  breakfast: 8 recipes
  lunch:     17 recipes
  dinner:    20 recipes
  snack:     0 recipes  -> auto-forced snack_optional=True
optimize relaxation level: 0 (strict, except meal_slot relaxed for snack)
optimize seconds: 0.05
optimize slack_total: 25,791  (Daily Dozen + nutrition slack)
plan_unique_ingredients: 52
plan_daily_dozen_violations: 103
plan output:
  reports/plan_1.md
  reports/plan_1.html
  reports/plan_1_kcal_fiber.png
  reports/plan_1_daily_dozen.png
  reports/plan_1_top_ingredients.png
non-plant recipes in plan: 0  (filter works correctly)
```

## Things still untested (need external resources)

These are all written and look right on paper, but I could not exercise
them in this environment.

| Area | Why blocked |
|---|---|
| GitHub Actions workflows | needs the PR / push to main to actually run. |
| Prefect deployment | needs Prefect Cloud or self-hosted server + worker. |
| LLM adapters (Anthropic/OpenAI) | no API key in this environment. |
| USDA fallback | no API key. |
| CoFID lookup with real data | no `data/cofid.xlsx` in repo (licence question deferred). |
| Pre-commit hooks | not installed in this venv (`pre-commit install` step). |

## Round 2 — Docker-up usability run (2026-05-05, after Docker started)

After Docker Desktop was started, the full Docker-based path was exercised.
This caught five additional issues, all fixed.

### What ran

| # | Test | Result |
|---|---|---|
| 1 | `docker compose up -d postgres` | passed (after volume reset) |
| 2 | `alembic upgrade head` against the docker container | passed |
| 3 | `pytest tests/integration -v` (testcontainers spinning up Postgres) | **fixed** then 13/13 passed |
| 4 | `docker build -t meal-planner:test .` | **fixed** twice, then succeeded |
| 5 | `docker run ... meal-planner:test run --ignore-coverage` end-to-end | **fixed** twice, then ran cleanly |
| 6 | Second consecutive run (cache warm) | 5.9s end-to-end |
| 7 | DB sanity checks (4 plan_runs, 0 non-plant, 19 metrics each correlated) | passed |

### Issues found (all resolved in this session)

#### Issue 5 — testcontainers can't talk to Docker on urllib3>=2 (`pyproject.toml`)
Symptom: every integration test errored with
`docker.errors.DockerException: Error while fetching server API version: Not supported URL scheme http+docker`.
Cause: `docker==6.1.3` (constrained transitively by older Prefect 2.16) is incompatible with urllib3 v2; testcontainers calls into docker-py.
Fix: Upgraded to `docker>=7.1` and added a hard pin in dev dependencies. Prefect-2.16's complaint is cosmetic (it pins `<7.0` in its own metadata but actually works fine with 7.x for the surface we use). After the bump, all 13 integration tests pass.

#### Issue 6 — Dockerfile builder failed on missing README.md (`Dockerfile`)
Symptom: `pip install ".[report]"` errored at `hatchling.metadata.core.readme` because hatchling validates the readme path declared in `pyproject.toml`.
Cause: README.md not COPY'd into the builder stage.
Fix: Added `README.md` to the builder COPY line.

#### Issue 7 — multi-stage `--prefix` install lost transitive deps (`Dockerfile`)
Symptom: container started fine but `meal-planner run` failed with
`ModuleNotFoundError: No module named 'packaging'` from matplotlib's import.
Cause: `pip install --prefix=/install` skips packages that the builder Python already has (like `packaging`, `wheel`). When the runtime stage doesn't have them either, imports fail.
Fix: Switched the builder stage to `python -m venv /opt/venv` and copy that whole venv into the runtime stage. Cleaner, fully self-contained.

#### Issue 8 — report writes a non-relocatable `plan_report.md` (`src/meal_planner/report/generate.py`)
Symptom: pipeline finished optimisation then died with
`PermissionError: [Errno 13] Permission denied: 'plan_report.md'` — the legacy duplicate write to the working directory failed because the container runs as a non-root user and `/app` is root-owned.
Cause: The report module wrote two copies of the markdown — one to `output_dir/plan_<id>.md` (good) and one to `./plan_report.md` (legacy compatibility, but bad on read-only filesystems / non-writable cwd).
Fix: Wrapped the legacy write in `try/except OSError: pass`. The canonical path stays under `output_dir`, the legacy path is best-effort. In the long run this duplicate write should just be deleted, but `try/except` is the lowest-risk fix.

#### Issue 9 — matplotlib needed a writable home directory (`Dockerfile`)
Symptom: `mkdir -p failed for path /home/app/.config/matplotlib: [Errno 13] Permission denied: '/home/app'`. Pipeline still completed (matplotlib falls back to /tmp), but the warning is loud and hints at fragility.
Cause: `useradd` for the `app` user didn't create a home directory.
Fix: `useradd -d /home/app -m app` and added `ENV MPLCONFIGDIR=/tmp/matplotlib HOME=/home/app` for clarity.

### Numbers from the containerised run

```
recipes ingested:                  178 (out of 180 HTML; 2 missing <h1>)
plant-based recipes:               96
ingredient lines:                  2,121
canonical resolution:              1,325 (62.5%) without LLM
plan runs persisted:               4
plan_meal rows:                    112 (4 runs * 28 slots)
plan_config snapshots:             4 (every run linked)
meal_history seeded:               14 rows
pipeline_metric distinct names:    19, each emitted 1x per run
non-plant in any plan:             0
relaxation level reached:          0 (strict) on every run
typical solve time:                30-50 ms
warm-cache run end-to-end:         ~6 seconds
docker build cold:                 ~14 seconds
```

### Things still untested after this round

| Area | Why blocked |
|---|---|
| GitHub Actions workflows | needs the PR / push to main to actually run on GitHub. |
| Prefect deployment | needs Prefect Cloud or self-hosted server + worker. |
| LLM adapters (Anthropic / OpenAI) | no API key configured. |
| USDA fallback | no API key. |
| CoFID lookup with real data | no `data/cofid.xlsx` in repo (licence question deferred). |
| Pre-commit hooks installed in this checkout | optional; not invoked yet. |
| FastAPI sidecar (`/healthz`, `/livez`, `/plan/latest`) | not started in this run. |

## Static checks after all fixes

```
mypy --strict src/meal_planner   ->  Success: no issues found in 42 source files
ruff check src tests             ->  All checks passed!
ruff format --check src tests    ->  66 files already formatted
pytest tests/unit                ->  89 passed
pytest tests/integration         ->  13 passed
```

## Round 3 — autonomous follow-up (2026-05-13)

Four of the previously-untested areas were exercised in this round. One more
bug surfaced and was fixed.

### What ran

| # | Test | Result |
|---|---|---|
| 1 | `pre-commit install` + `pre-commit run --all-files` | **fixed** then clean |
| 2 | FastAPI sidecar — `python -m meal_planner.serve` + `/livez`, `/healthz`, `/plan/latest` | passed |
| 3 | Prefect 2.16 — direct flow invocation against ephemeral local server | **fixed** then COMPLETED |
| 4 | Prefect 2.16 — `prefect server start` + `prefect worker start` + deployment run via API | COMPLETED in 7.4s |
| 5 | GitHub Actions — querying public API for runs on `claude_final` | 0 runs found (workflows present but require PR-to-main or push-to-main to fire) |

### Issues found

#### Issue 10 — Prefect 2.16 unimportable due to `griffe` upgrade (`pyproject.toml`)
Symptom: `from griffe.dataclasses import Docstring` → `ModuleNotFoundError: No module named 'griffe.dataclasses'`.
Cause: `griffe` 0.40+ removed the `dataclasses` module; Prefect 2.16 still imports the old path.
Fix: pinned `griffe<0.40` in the `orchestrate` extras group.

#### Issue 11 — pre-commit caught remaining ruff lints (`alembic/env.py`)
Symptom: `SIM103 Return the negated condition directly`.
Fix: simplified `include_object` to a single `return not (...)`.

#### Observed but not fixed
- Prefect 2.16 `prefect deployment run` CLI is broken — `TypeError: 'NoneType' object is not iterable` in `_load_json_key_values` whether you pass `--params`, `--param`, or nothing. Worked around by POSTing to the `/api/deployments/<id>/create_flow_run` REST endpoint directly. This is a known Prefect 2.16 bug that would be moot after upgrading to Prefect 3.

### Successful Prefect flow run

```
flow: weekly-meal-plan
deployment: weekly-meal-plan/weekly-meal-plan
work pool: default (process)
flow_run_id: 3901dd50-fed7-45b5-bd7b-f016b9343318
state: COMPLETED
total_run_time: 7.45s
tasks executed: inventory -> ingest -> parse -> nutrition -> optimise -> report
final state: plan_run persisted, reports/plan_*.md and reports/plan_*.html generated
```

### Successful FastAPI sidecar

```
$ python -m meal_planner.serve
GET /livez            -> 200 {"status":"live"}
GET /healthz          -> 200 {"status":"ok"}
GET /plan/latest      -> 200 (text/html, 2,131 bytes, renders latest plan)
```

### GitHub Actions

Workflows present on the remote `claude_final` branch
(`build.yml`, `lint.yml`, `migrate.yml`, `security.yml`, `test.yml`). They
trigger on:
- `pull_request` (lint, test, migrate, security)
- `push` to `main` (lint, test, build)
- `schedule` (security only)

Since `claude_final` is neither `main` nor part of an open PR, no runs have
fired. To exercise CI, open a PR `claude_final → main` — the lint, test,
migrate, and security workflows will all run on the PR.

### Things still untested

| Area | Status |
|---|---|
| LLM adapters (Anthropic / OpenAI) | waiting for user to drop `LLM_API_KEY` into `.env` |
| USDA fallback | waiting for `USDA_API_KEY` |
| CoFID lookup | waiting for `data/cofid.xlsx` |
| GitHub Actions on a real PR | will run automatically when a PR is opened |

## Round 4 — Anthropic + CoFID + USDA wired (2026-05-16)

Credentials and data were dropped into `.env` and `data/cofid.xlsx`. Four
bugs surfaced and were fixed; the headline result is the full pipeline now
achieves **96.6% nutrition coverage**, vs 0% in earlier rounds, and the
optimiser solves with **slack 584** vs ~25,800 previously.

### Bugs found

#### Issue 12 — Anthropic SDK 0.34 vs current `httpx` (`pyproject.toml`)
Symptom: `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`.
Cause: `anthropic 0.34.2` passes a `proxies=` kwarg into `httpx.Client` which newer httpx removed.
Fix: bumped `llm-anthropic` extra to `anthropic>=0.70`.

#### Issue 13 — CoFID multi-sheet workbook, wrong sheet read (`src/meal_planner/nutrition.py`)
Symptom: `_load_cofid` returned only the "List of tables" worksheet (15 rows, 2 columns), so every ingredient lookup failed.
Fix: `_load_cofid` now scans every sheet and picks the one whose columns yield both a `name_col` and the most nutrient columns. For CoFID 2021 this finds `1.3 Proximates` (2,889 food rows, 47 columns).

#### Issue 14 — CoFID uses string sentinels for missing data (`src/meal_planner/nutrition.py`)
Symptom: known foods like "Curly kale, raw" came back with `fibre=None` even though the row had a value.
Cause: AOAC fibre column uses the literal string `"N"` for not-measured. Our `pd.isna` check only catches `NaN`.
Fix: `_row_value` now also treats `"N"`, `"Tr"`, `"-"`, `""` as missing. Also extended `_guess_columns` to prefer the AOAC fibre column and fall back to NSP (the older UK fibre measure).

#### Issue 15 — Fuzzy matches picked composite dishes over raw ingredients (`src/meal_planner/nutrition.py`)
Symptom: `oats → buckwheat groats`, `spinach → cabbage and spinach bhaji`, `lentils → aubergine stuffed with lentils`.
Cause: rapidfuzz WRatio scoring against full food names rewarded substring containment in long composite-dish names.
Fix: introduced `_base_name(name)` that builds match candidates from the first two comma-separated tokens (CoFID convention: `"<food>, <variant>, <prep>, ..."`); added a `_PREFERRED_QUALIFIERS` boost for `raw / dried / fresh / uncooked`; added a `_DEMOTED_TERMS` penalty for `with / stuffed / curry / pilau / salad / fried / roasted / soup / stew / homemade`; applied a small length penalty as a tie-breaker. Same scoring is now applied to USDA results too (bumped USDA `pageSize` from 1 to 10 and ranked locally).

### LLM-friendlier env var fallback (`src/meal_planner/llm/factory.py`)
The Anthropic adapter now reads `LLM_API_KEY` first, then falls back to `ANTHROPIC_API_KEY`; the OpenAI adapter similarly falls back to `OPENAI_API_KEY`. The user dropped `ANTHROPIC_API_KEY` and `USDA_API_KEY` into `.env`; the code finds both automatically without renaming.

### Targeted smoke tests

```
Anthropic LLM adapter on 5 hard lines:
  1 small red onion, finely chopped     -> red onion       (Other Vegetables, 1 small)
  200g tenderstem broccoli               -> tenderstem broccoli (Cruciferous Vegetables, 200 g)
  1 tbsp gochujang paste                 -> gochujang paste (Herbs and Spices, 1 tbsp)
  2 sheets nori                          -> nori             (Greens, 2 sheets)
  100g vermicelli noodles                -> vermicelli noodles (Whole Grains, 100 g)
  (5/5 parsed, 0 invalid_json)

CoFID lookups (after fixes):
  kale -> curly kale, raw            kcal=33
  brown rice -> rice, brown, basmati, raw    kcal=355
  oats -> porridge oats, unfortified  kcal=381  (fibre=7.8 g)
  blueberries -> blueberries          kcal=40
  lentils -> lentils, red, split, dried, raw  kcal=311
  broccoli -> broccoli, green, raw    kcal=34
  garlic -> garlic, raw               kcal=98
  quinoa -> quinoa, raw               kcal=309
  kidney beans -> beans, red kidney, dried, raw  kcal=266

USDA fallback (after pageSize=10 + ranking):
  kale -> Kale, raw          kcal=35
  miso -> Miso               kcal=198 fibre=5.4
  chia seeds -> Chia seeds, dry, raw     kcal=517
  tempeh -> Tempeh           kcal=192
```

### Full pipeline (Round 4)

```
ingest:    180 files -> 180 recipes, 2,121 ingredient lines, 83 non-plant filtered
parse:     2,121 rows; 560 fell through to Anthropic; 0 invalid_json; ~10 minutes total
nutrition: 1,098 items, 1,061 covered = 96.6% (coverage gate threshold is 60% -> passes)
optimise:  relaxation_level=0 (strict), 300s (hit time limit -> returned 'feasible'),
           slack_total = 584 (vs 25,791 before)
plan:      plan_run_id=1, 84 unique ingredients (vs 55 before),
           total_kcal = 13,688 / week (avg ~1,956/day),
           total_fiber = 186 g / week (avg ~26/day),
           daily Dozen violations = 98 (still some, mostly due to unique-ingredient-per-day rule),
           non-plant picks in plan: 0
recipe_nutrition: 150 recipes with kcal>0, avg 1,415 kcal / 20.1 g fibre per recipe
report:    reports/plan_1.md, reports/plan_1.html generated; legacy plan_report.md written
```

### Still outstanding

Adding 7 entries to `config/ingredient_synonyms.csv` (flax↔flaxseed, chickpeas↔chick peas) would push USDA/CoFID match quality further, but is no longer load-bearing on the coverage gate.

## Round 5 — same-day duplicate bug (2026-05-16)

Round 4 output for day 2 showed `Lunch: Chickpea & Cauliflower Curry` and
`Dinner: Chickpea & Cauliflower Curry` — the same recipe filling two slots
on the same day. Flagged by user as unacceptable.

### Issue 16 — same recipe could occupy multiple slots on one day (`src/meal_planner/optimize/model.py`)

Cause: the model had `meal_slot` (exactly 1 recipe per (d, m)) and
`repeat_limit` (recipe r appears at most `max_recipe_repeats` times across
the week), but nothing forcing distinctness *within* a day. Under
nutrition pressure, the optimiser could maximise its objective by filling
both lunch and dinner with the same calorically-dense recipe.

Fix: added `one_recipe_per_day` constraint
`sum_m x[r, d, m] <= 1` for every (recipe, day).

Regression test added in `tests/integration/test_optimise.py`:
`test_no_recipe_appears_twice_on_same_day` walks the returned plan and
asserts `len(picks) == len(set(picks))` for each day.

### Re-run with the fix

```
plan_run_id=2 unique_ingredients=90 (vs 84) violations=93 (vs 98)
slack_total=450 (vs 584)
DB check: every day has 3 picks / 3 distinct recipes
```

Day 2 example after the fix:

```
Breakfast: Berry and Banana Smoothie Bowl
Lunch:     Healing Miso Soup
Dinner:    Plant-Powered Polenta Ragu
Calories:  2021 kcal | Fibre: 31.3 g
```

### Side observation (not fixed)

Two adjacent days in the Round 5 plan were identical
(`Blueberry-Banana-Flaxseed Smoothie Bowl / 4-Bean Chilli / Weeknight
Minestrone Soup` on both day 4 and day 5). That isn't the same-day bug —
it's the across-week-repeat-cap (`max_recipe_repeats=2`) allowing both
recipes to be chosen for two consecutive days, and on a fresh DB the
recency term contributes 0 (no `meal_history`). Possible follow-ups, none
load-bearing:
1. Add a soft penalty on consecutive-day repeats.
2. Lower `max_recipe_repeats` to 1.
3. After the first plan is written, the recency term naturally encourages
   variety in week 2 onward; the recency penalty isn't yet engaged on the
   first run.

## Recommended follow-up

1. **Default snack-handling test in unit suite** — the `_maybe_force_snack_optional` path is now important; add an integration test that exercises a corpus with no snack recipes.
2. **Coverage-gate test** — assert that nutrition step raises with `coverage < 0.6` and the right error message.
3. **Improve parse hit rate** — 62% canonical resolution without LLM is meh. The fuzzy matcher is missing a lot of multi-word ingredients (e.g., "1 small red onion, finely chopped"). Consider:
   - tokenising the cleaned name and trying each token against the canonical list,
   - extending `ingredient_synonyms.csv` with the common producer-style phrasings,
   - tightening `_clean_for_fuzzy` analogue inside `parse.py` (it currently only normalises whitespace+case).
4. **Ship a CoFID file** (or document the bootstrap) so the coverage gate can fire usefully on first run.
5. **Local-development quickstart in README** — mention `DB_HOST=localhost`. The current quickstart implies docker-compose, which is fine, but a sentence for bare-metal devs would prevent confusion.
