# Runbook: USDA throttled

## Symptoms
- `parse_llm_used` is normal but `nutrition_coverage_ratio` drops well below
  the configured threshold.
- Logs show `nutrition.usda_failed` or HTTP 429s.

## Triage
1. Confirm USDA quota status: https://fdc.nal.usda.gov/api-key-signup.html
2. Check structured logs filtered to nutrition step:
   ```bash
   meal-planner config print --format json | jq .nutrition
   ```
3. Inspect the latest run's coverage in `pipeline_metric`.

## Recovery
- Switch to CoFID for the next run by ensuring `data/cofid.xlsx` is in place
  and `nutrition.cofid_path` points at it.
- Reduce throughput: lower batch sizes are not applicable to nutrition (it's
  per-ingredient), but you can pre-warm the cache by running once with
  `--ignore-coverage` to populate `ingredient_nutrition_cache`.
- Rotate to a fresh USDA API key if you've exceeded daily quota.

## Prevention
- Cache hit ratio is the key metric — once `ingredient_nutrition_cache` is
  warm, USDA traffic should be near zero. New ingredients are the only ones
  that hit USDA.
- Consider committing CoFID to the repo (LFS) if you cannot redistribute,
  document the bootstrap step in the README.
