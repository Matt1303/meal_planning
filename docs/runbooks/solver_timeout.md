# Runbook: solver timeout

## Symptoms
- `meal-planner run` finishes but `relaxation_level >= 1` consistently.
- `solver_seconds` ≈ `solver_time_limit` for every run.
- `slack_total` is large.

## Triage
1. Inspect the variable count:
   ```sql
   SELECT metric_value FROM meal_planning.pipeline_metric
   WHERE metric_name = 'solver_variable_count' ORDER BY metric_time DESC LIMIT 5;
   ```
2. Check how many recipes the optimiser actually sees:
   ```sql
   SELECT count(*) FROM meal_planning.recipe WHERE is_plant_based = TRUE;
   ```
3. Look at constraint slack to identify which constraint is straining:
   ```sql
   SELECT * FROM meal_planning.plan_run ORDER BY run_time DESC LIMIT 5;
   ```

## Recovery
- Bump `optimizer.solver_time_limit` (in seconds) and re-run.
- Loosen `optimizer.solver_mip_gap` (e.g., 0.1) to accept "good enough" plans.
- Drop a Daily Dozen target temporarily if the corpus is too small.

## Prevention
- Monitor `solver_seconds` over time. If it climbs as the recipe corpus
  grows, look at filtering ineligible recipes earlier (e.g., low-rating
  exclusion is already in place).
- The relaxation hierarchy is your safety net — at worst, the strict run
  will time out and the next level kicks in. `relaxation_level > 0` runs
  are still useful but should be alerted on if persistent.
