# Configuration

All pipeline knobs live in `config/pipeline.yaml`. Settings are loaded into
typed pydantic-settings models (`Settings.load(path)`); `meal-planner config validate`
fails fast on invalid inputs.

The YAML path is overridden by `PIPELINE_CONFIG` env var or `--config <path>`
CLI flag.

## Top-level keys

### `sources.local_html`

| key | meaning | default |
|---|---|---|
| `path` | directory of `*.html` recipe files | required |
| `selectors.title` | CSS selector for the recipe title | `"h1"` |
| `selectors.ingredient_lines` | selector returning ingredient `<p>` tags | required |
| `selectors.category` | meal-type tag selector | required |
| `selectors.rating` | numeric rating attribute selector | required |
| `selectors.servings` | yield selector (handles "4-6", "makes 12", "1.5 dozen") | required |
| `selectors.difficulty` | difficulty selector | required |

### `parse`

| key | meaning | default |
|---|---|---|
| `food_list_paths` | list of canonical food list files (later wins on conflict) | `[config/food_list_canonical.txt]` |
| `synonyms_path` | CSV `raw,canonical` for explicit overrides before fuzzy match | `config/ingredient_synonyms.csv` |
| `unit_grams_path` | CSV `unit,grams_per_unit,note` driving `_unit_to_grams` | `config/unit_grams.csv` |
| `density_path` | CSV `ingredient_canonical,density` for volume→grams conversion | `config/density_g_per_ml.csv` |
| `non_plant_terms_path` | YAML used by the plant classifier at ingest | `config/non_plant_terms.yaml` |
| `fuzzy_min_score` | minimum rapidfuzz score (0–1) to accept a match | `0.8` |
| `llm_threshold` | unused threshold reserved for future scoring | `0.7` |

### `meal_types`

Ordered list of meal slot names; defaults to
`[breakfast, lunch, dinner, snack]`. Must match the keys in
`MEAL_TYPE_NORMALIZE`.

### `portion_sizes` and `daily_dozen_targets`

Both maps must have the **same keys** — config validation enforces that
every Daily Dozen group has a portion size declared. Tweaking these to
match a specific dietary template (e.g. raising "Whole Grains" daily target
to 4) is supported; the optimiser picks them up on the next run.

### `nutrition`

| key | meaning |
|---|---|
| `cofid_path` | local path to the CoFID Excel/CSV (UK gov nutrition data) |
| `cofid_url` | optional download URL used by `meal-planner data fetch-cofid` |
| `usda_api_key` | falls back to `USDA_API_KEY` env var |
| `coverage_min_ratio` | run fails if (covered/total) < this; `--ignore-coverage` overrides |

### `llm`

| key | meaning | default |
|---|---|---|
| `provider` | `anthropic`, `openai`, or empty for `NullLLM` | `anthropic` |
| `model` | provider-specific model name | `claude-sonnet-4-6` |
| `api_key` | overrides `LLM_API_KEY` env var | empty |
| `batch_size` | lines per LLM call | `5` |

### `optimizer`

Tuning knobs for the MILP. Validated for sane ranges (e.g.
`calories_daily_min <= calories_daily_max`).

| key | role |
|---|---|
| `min_rating` | recipes below this are excluded |
| `rating_weight` | objective coefficient |
| `diversity_weight` | objective coefficient on `Σ y[i]` |
| `recency_weight` | penalty on recently-planned recipes |
| `slack_weight` | penalty on every slack variable |
| `recency_half_life_days` | controls `exp(-Δ / h)` decay |
| `calories_daily_min` / `max` | per-day kcal soft bounds |
| `fiber_daily_min` | per-day fiber soft bound |
| `calories_weekly_min` / `max` / `fiber_weekly_min` | weekly soft bounds |
| `weekly_group_portions_min` | optional per-group weekly portion floor |
| `snack_optional` | allows snack slot to be filled by slack |
| `max_recipe_repeats` | cap on appearances per week |
| `solver_time_limit` | GLPK `tmlim` (seconds) |
| `solver_mip_gap` | GLPK `mipgap` |
| `planning_horizon_days` | usually 7 |
| `include_non_plant` | bypasses the plant-only filter |

## Environment variables

See `.env.example` for the list. Database connection comes from `DB_USER`,
`DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` (with sensible defaults so
local development works). `LLM_API_KEY` and `USDA_API_KEY` are read by their
respective adapters when YAML doesn't supply a value. `LOG_LEVEL` and
`LOG_FORMAT=json|console` control structlog rendering.
