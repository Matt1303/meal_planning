# Meal Planner — User Guide

A practical guide to refreshing your plan from Paprika and understanding how the
optimiser, nutrition and Daily Dozen logic actually work.

---

## 1. The refresh workflow (Paprika → plan)

Your recipes live in the **Paprika** app. The planner reads an HTML export of
those recipes, enriches them with nutrition data, and solves for a weekly plan.

> **Prerequisite:** Docker Desktop running. Docker hosts the Postgres database
> (and nothing else — the pipeline itself runs natively in `.venv`). The refresh
> script starts the Postgres container for you if it isn't already up.

### Step by step

1. **Edit recipes in Paprika** — add, change, delete, re-rate, adjust servings.
2. **Export to HTML:**
   - Select all recipes (`Cmd-A` in the recipe list).
   - Click **Export**.
   - Choose **HTML** format.
   - Save into your export folder (default: `~/MealPlanAutomation`). Each export
     creates a new folder, e.g. `190 recipes/`.
3. **Run the refresh:**
   ```bash
   make paprika-refresh
   ```
   or directly:
   ```bash
   ./scripts/paprika_refresh.sh
   ```
4. **View the result** in the Streamlit dashboard (`make ui` or the running
   instance at http://127.0.0.1:8501/).

That's it — one manual export, one command.

### What `paprika-refresh` does

1. Finds the **most recently modified** export folder under
   `~/MealPlanAutomation` (so you don't have to delete old ones — it always
   uses the newest).
2. `rsync`s that export's `Recipes/` folder into the repo's
   `recipes_html/Recipes/`, **deleting** recipes you removed in Paprika so the
   database stays in step.
3. Loads `.env` for credentials and forces the DB host to `localhost` (the
   `.env` ships `DB_HOST=postgres` for in-container use; on your Mac the
   database is reached on `localhost`).
4. **Ensures Postgres is running** — if it can't reach the database it starts
   the Docker `postgres` service and waits for it. (Docker Desktop must be
   running; if it isn't, you get a clear message instead of a hang.)
5. Applies any pending DB migrations (`alembic upgrade head`).
6. Runs the full pipeline: `meal-planner run`.

So from a cold terminal the only prerequisite is **Docker Desktop running** —
no manual `docker compose up`, no env juggling.

### Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `PAPRIKA_EXPORT_DIR` | `~/MealPlanAutomation` | Where Paprika exports land |
| `RECIPES_HTML_DIR` | `<repo>/recipes_html` | Pipeline source dir |
| `SKIP_PIPELINE=1` | unset | Sync files only, skip the pipeline (for testing) |

### Speed: why a refresh is usually fast

The two slow stages are **cache-backed**, so after editing a handful of recipes
only genuinely *new* data is recomputed:

- **Parsing** is cached per ingredient line (`ingredient_parse_cache`).
- **Nutrition** is cached per canonical ingredient (`ingredient_nutrition_cache`).

A cold run (empty caches) calls Claude for every unique ingredient and takes
~10–15 minutes. A typical refresh after editing a few recipes finishes in
seconds-to-a-minute because almost everything is already cached. Only the
**optimise** step always runs in full (it produces a fresh plan each time).

---

## 2. The pipeline stages

`meal-planner run` chains five stages (see `run_pipeline.py`):

```
ingest → parse → enrich (nutrition) → optimise → report
```

| Stage | What it does |
|---|---|
| **ingest** | Reads `recipes_html/Recipes/*.html`, pulls title, ingredient lines, rating, servings, categories into the `recipe` / `recipe_ingredient` tables. Plant-only recipes are kept (non-plant filtered unless `include_non_plant`). |
| **parse** | Turns each raw ingredient line into a quantity + unit + canonical ingredient + food group. Uses quantulum + regex for quantities, per-piece / density tables for grams, fuzzy matching + an LLM fallback for the canonical name. |
| **enrich** | Attaches per-100g macros to each canonical ingredient, then rolls them up to per-serving and per-recipe nutrition. (See §4.) |
| **optimise** | Solves the MILP for a weekly plan that hits your targets. (See §3.) |
| **report** | Writes a Markdown/HTML report of the chosen plan. |

---

## 3. The optimiser — how meals are chosen

The optimiser is a **Mixed-Integer Linear Program (MILP)** solved with GLPK
(`optimize/model.py`). It picks one recipe per meal slot, per day, per person,
to **maximise an objective** subject to **nutrition and variety constraints**.

### 3.1 Recipe eligibility (before solving)

Recipes are filtered out before the solve if:

- rating is below `min_rating` (default **3** out of 5), or
- the recipe is non-plant (unless `include_non_plant: true`).

So a recipe you rate 1–2 stars in Paprika will simply never appear.

### 3.2 The objective (what "a good plan" means)

The solver **maximises**:

```
  diversity_weight  × diversity
+ rating_weight     × rating_term
- recency_weight    × recency_term
- slack_weight      × slack
- spacing_weight    × spacing_term
```

| Term | Meaning | Default weight |
|---|---|---|
| **diversity** | Count of *distinct* recipes used per person — rewards variety across the week. | `1.0` |
| **rating_term** | Sum of (recipe rating × times it appears). Higher-rated recipes are preferred. | `1.0` |
| **recency_term** | Penalises recipes eaten recently. `recency = exp(−days_since / half_life)`, so something eaten yesterday scores ~1 (big penalty) and something eaten 60 days ago scores near 0. | `0.8`, half-life **30 days** |
| **slack** | Penalty for *missing* a nutrition or food-group target (see relaxation below). Heavily weighted so the solver tries hard to hit targets. | `5.0` |
| **spacing_term** | Penalty for repeating the same recipe on **near** days, decaying with the gap. | `2.0` |

**Rating weight is tunable live** in the Streamlit sidebar — turn it up to lean
harder on your favourites, down to spread the net wider.

#### Spacing penalty (avoiding "same meal two days running")

When a recipe repeats within the week, the penalty depends on how many days
apart the repeats are:

| Gap | Penalty factor |
|---|---|
| 1 day (consecutive) | `1.0` (worst) |
| 2 days | `0.3` |
| 3 days | `0.1` |
| 4+ days | `0` (no penalty) |

So the solver will happily reuse a great recipe, but it pushes the repeats
**3–4 days apart** rather than back to back.

### 3.3 The constraints (what a plan *must* satisfy)

- **One recipe per day per person** — the same dish can't fill two slots on the
  same day (no "Chickpea Curry for both lunch and dinner").
- **Max repeats per week** — any recipe appears at most `max_recipe_repeats`
  times (default **2**).
- **Shared vs personal meals** — in two-person households, the *shared* meal
  types (default lunch + dinner) are the **same dish for both people**, while
  breakfast and snack are chosen per person. This keeps cooking practical while
  letting each person hit different calorie/protein targets.
- **Daily nutrition** — each person's day must land within their calorie range
  and above their fibre / protein minimums (with slack — see below).
- **Weekly nutrition** — weekly calorie / fibre / protein envelopes.
- **Daily Dozen food-group targets** — daily and weekly portion targets per
  group (see §5).

### 3.4 The relaxation ladder (what happens when targets can't all be met)

If your recipe set genuinely *can't* satisfy every constraint (e.g. not enough
high-fibre recipes to hit 30 g every single day), the solver doesn't just fail.
It tries a **ladder of progressively looser models** and uses the first that's
feasible:

1. **STRICT** — enforce everything.
2. **DROP_DAILY_NUTRITION** — keep weekly nutrition + all group targets, relax
   the per-day calorie/fibre/protein hard limits (they become soft slack).
3. **DROP_WEEKLY_GROUPS** — also relax weekly nutrition + weekly group targets.
4. **DROP_GROUP_TARGETS** — also relax the daily group targets.
5. …continuing until a feasible plan exists.

The **Relaxation** number shown in the dashboard header tells you how far down
the ladder it had to go. `0` = every target met strictly. Higher = some targets
were softened, and the **slack total** quantifies by how much. A high slack/
relaxation is a signal you need more recipes of a certain type (more high-fibre
meals, more berries, etc.).

### 3.5 Fixed meals (e.g. the same breakfast every day)

You can **pin a recipe to one person's breakfast every day** — useful when
someone always has the same thing each morning. In the sidebar, each user has a
**"fixed breakfast"** dropdown; pick a recipe and that person gets it every day,
while the optimiser plans their lunch/dinner/snack *around* it (it still counts
the fixed meal's calories and macros toward the day's targets). The other person
is unaffected and their breakfast is chosen freely. A pinned recipe is exempt
from the repeat cap and spacing penalty (it's meant to repeat). Combine this
with a Paprika Nutrition section (§4) so the fixed meal's macros come straight
from the recipe.

### 3.6 Shortfall handling (snacks)

When the solver can't reach a person's calorie/protein/fibre minimum from the
recipes alone, the dashboard surfaces the remaining gap on that day's **snack**
slot ("source your own to top up — ~180 kcal / 12 g protein short") so you can
add a shake, yoghurt, etc. yourself.

---

## 4. Nutrition data — where the numbers come from

Per-ingredient macros are resolved in this priority order (`nutrition.py`):

1. **Claude (primary)** — for each unique canonical ingredient we ask Claude for
   realistic per-100g values *as the food is used in a recipe*. This handles the
   things product databases get wrong: prepared stock is ~5 kcal/100g (not the
   dried cube), plant milks are the drink form (not the whole nut), tinned
   tomatoes are passata-like, fresh onion is raw (not granules). Results at or
   above the confidence threshold are cached as `claude_high` / `claude_medium`.
2. **Open Food Facts** — fallback for anything Claude is unsure about.
3. **CoFID** (UK Composition of Foods) — fallback.
4. **USDA FoodData Central** — final fallback.

After the first pass, a **second LLM verification** sanity-checks low-confidence
matches and either approves, rejects, or proposes a better search term.

### Cooking-oil absorption

Oils used for cooking (olive, sunflower, coconut, ghee, …) are scaled by an
**absorption factor** (default `0.5`) — most of the oil stays in the pan, so
2 tbsp of olive oil contributes ~half its raw calories.

### Sub-recipes ("separate recipe" lines)

An ingredient line like `340 grams Roasted Italian Medley (separate recipe)`
points at **another recipe**, not a raw ingredient. The parser links it to that
recipe and the nutrition step expands the sub-recipe's ingredients into the
parent meal, scaled to the grams on the line. In the per-meal popover these
show as `recipe: <Sub Recipe Title>`.

> Note: a few companion recipes referenced this way aren't in your Paprika
> library (e.g. "Light Vegetable Broth"), so they can't be expanded — add them
> to Paprika to close those gaps.

### Paprika Nutrition section (declared nutrition)

If a recipe has a **Nutrition** section filled in inside Paprika (e.g.
"592 calories, 67g carbohydrate, 12g fat, 48g protein, 13g fibre"), the planner
uses those **declared per-serving values verbatim** and does *not* try to
compute the recipe from its ingredients. This is ideal for things like a fixed
breakfast smoothie that's always made the same way — you control the numbers
directly in Paprika. Any macro you leave blank in Paprika falls back to the
ingredient-computed value, so you can fill in just calories + protein and let
the rest be estimated.

### Coverage

The dashboard and logs report **coverage** = fraction of ingredient instances
with nutrition attached. We deliberately **reject junk matches** rather than
pad totals with nonsense, so coverage in the mid-80s% with *correct* numbers is
better than 97% with a stock cube counted as 300 kcal.

---

## 5. The Daily Dozen

Based on Dr Greger's *How Not to Die*, the **Daily Dozen** is a set of food
groups you aim to hit **every day**. Targets are configured in
`daily_dozen_targets` (portions/day):

| Group | Target/day |
|---|---|
| Beans | 3 |
| Berries | 1 |
| Other Fruits | 3 |
| Cruciferous Vegetables | 1 |
| Greens | 2 |
| Other Vegetables | 2 |
| Flaxseeds / Linseeds | 1 |
| Nuts and Seeds | 1 |
| Herbs and Spices | 1 |
| Whole Grains | 3 |

Each parsed ingredient is mapped to a food group, and its grams are converted to
"portions" via `portion_sizes`. The optimiser treats these as daily (and weekly)
targets with slack.

### Reading the dashboard

The **Daily Dozen alignment** section shows:

- **Three metric cards** — groups *fully met every day* / *partial* / *gap*.
- A **gap warning** naming any group with zero portions across the plan.
- A **days-hit bar chart** — for each group, how many days it reached its daily
  target, coloured red (zero) → amber (partial) → green (every day), with a
  dashed line at "every day".
- A **per-day heatmap** (in an expander) for the day-by-day detail.

If a group is a persistent gap (e.g. Berries always zero), that's a content
problem, not a solver problem — add more recipes containing that group in
Paprika, or raise the group's weight, and re-refresh.

---

## 6. Settings — where and how to change things

There are **three** places a setting can live. Use whichever is easiest for what
you're doing:

| Place | Best for | Permanent? |
|---|---|---|
| **Streamlit sidebar** | Quick experiments — "what if I want more protein?" | No — applies to the current screen only, until you reload |
| **`config/pipeline.yaml`** | The defaults you always want | Yes — every run uses them |
| **Command-line flags** | One-off runs | No — just that one command |

### 6.1 The config file (`config/pipeline.yaml`)

This is the master settings file — a plain-text file you can open in any editor
(it's in the project folder under `config/`). Settings are grouped under
headings like `optimizer:` and `nutrition:`. The format is **YAML**: a
`name: value` pair on each line, and **indentation matters** (each setting under
`optimizer:` is indented two spaces).

To change a setting, find its line and edit the value after the colon. For
example, to only ever include recipes you've rated 4 stars or higher, and to
allow non-plant recipes:

```yaml
optimizer:
  min_rating: 4            # was 3
  ...
  include_non_plant: true  # was false
```

Save the file, then run `make paprika-refresh` (or `make run`) and the new
values take effect. A few safety notes for non-programmers:

- Keep the **indentation** exactly as it was (two spaces, not a tab).
- Don't add quotes around numbers or `true`/`false`.
- `true`/`false` must be lower-case.
- If a run later complains about the config, you probably changed indentation or
  mistyped a value — undo your edit and try again.

### 6.2 The most useful settings (plain English)

All of these live under `optimizer:` in `config/pipeline.yaml` unless noted.

| Setting | What it does | Default |
|---|---|---|
| `min_rating` | Lowest Paprika star rating a recipe can have and still be used. Raise to be pickier. **Also a live slider in the sidebar.** | `3` |
| `include_non_plant` | `false` = plant-based recipes only; `true` = allow everything. **Also a `--include-non-plant` CLI flag.** | `false` |
| `calories_daily_min` / `calories_daily_max` | The daily calorie window each person should land in. **Also sidebar sliders, per person.** | `1800` / `2400` |
| `fiber_daily_min` | Minimum grams of fibre per day. **Also a sidebar control.** | `30` |
| `max_recipe_repeats` | How many times one recipe may appear in the week. **Also a sidebar slider.** | `2` |
| `planning_horizon_days` | How many days the plan covers. **Also a sidebar slider.** | `7` |
| `rating_weight` | How strongly the planner favours your higher-rated recipes. Higher = leans harder on favourites. **Also a sidebar slider.** | `1.0` |
| `diversity_weight` | How strongly it rewards variety (more distinct recipes across the week). | `1.0` |
| `recency_weight` | How strongly it avoids recipes you ate recently. | `0.8` |
| `recency_half_life_days` | "Recently" means roughly this many days — a recipe eaten this long ago is treated as half-forgotten. | `30` |
| `spacing_weight` | How hard it pushes repeats of the same recipe apart (3–4 days). **Also a sidebar slider.** | `2.0` |
| `slack_weight` | How hard it tries to *hit* nutrition/food-group targets vs. softening them. Higher = tries harder. | `5.0` |
| `calories_weekly_min` / `max`, `fiber_weekly_min` | Weekly envelopes (the daily ones usually dominate). | see file |
| `solver_time_limit` | Max seconds the solver may spend before giving its best answer. | `300` |

> The "weights" (rating, diversity, recency, spacing, slack) are the relative
> pulls in the optimiser's scoring (see §3.2). You rarely need to touch them —
> start with the targets (`min_rating`, calories, fibre, repeats) which have the
> most obvious effect.

Nutrition-related settings live under `nutrition:` (see §4) — e.g.
`cooking_oil_absorption` (default `0.5`) and `llm_macros_min_confidence`
(`medium`). These have sensible defaults and most users never change them.

### 6.3 The sidebar (fastest for experiments)

When the dashboard is open, the left sidebar lets you change the common settings
**without editing any files**:

- kcal / protein / fibre targets — **per person**
- two-user toggle + which meals are shared (lunch/dinner)
- rating weight and minimum rating
- spacing penalty weight and max repeats per week
- planning horizon (days)

Change anything, then click **Generate plan**. It re-solves using the
already-enriched nutrition data, so it's fast (a few seconds). These changes are
*temporary* — they don't write back to `config/pipeline.yaml`. When you find a
combination you like, copy those values into the config file to make them stick.

### 6.4 Command-line flags (one-off runs)

For a single run without changing anything permanently:

```bash
meal-planner run --include-non-plant   # allow non-plant recipes, just this once
meal-planner run --ignore-coverage     # don't stop if nutrition coverage is low
```

Run `meal-planner run --help` to see all flags.

---

## 7. Quick reference

```bash
# Full refresh from the newest Paprika export
make paprika-refresh

# Sync files only, don't run the pipeline (testing)
SKIP_PIPELINE=1 ./scripts/paprika_refresh.sh

# Run the pipeline without re-syncing from Paprika
make run            # = meal-planner run

# Open the dashboard
make ui

# See all CLI commands (ingest, parse, optimise, report, health, ui, …)
meal-planner --help
```
