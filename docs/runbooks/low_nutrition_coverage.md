# Runbook: low nutrition coverage

## Symptoms
- `meal-planner run` exits with `nutrition coverage X% is below threshold Y%`.
- `pipeline_metric.nutrition_coverage_ratio` < 0.6.

## Triage
1. Find the unmatched ingredients:
   ```sql
   SELECT DISTINCT ri.ingredient_canonical
   FROM meal_planning.recipe_ingredient ri
   LEFT JOIN meal_planning.ingredient_nutrition_cache nc
     ON nc.ingredient_canonical = ri.ingredient_canonical
   WHERE ri.ingredient_canonical IS NOT NULL AND nc.ingredient_canonical IS NULL
   ORDER BY ri.ingredient_canonical;
   ```
2. Confirm CoFID is loaded:
   ```bash
   ls -la $(meal-planner config print --format json | jq -r .nutrition.cofid_path)
   ```
3. Check USDA key:
   ```bash
   echo "${USDA_API_KEY:-MISSING}"
   ```

## Recovery
- Add manual overrides for the most common missing ingredients (e.g. ethnic
  ingredients CoFID doesn't know):
  ```bash
  meal-planner override add --raw "tahini" --canonical "tahini" --group "Nuts and Seeds"
  ```
- Lower the threshold for one run with `--ignore-coverage` to unblock the
  weekly plan, then drive down the gap.
- Run `meal-planner nutrition refresh --all` after bumping the CoFID file.

## Prevention
- Review `ingredient_nutrition_cache` periodically and prune obviously bad
  matches (e.g. very low `match_score`).
- A new "non-canonical ingredient" alert from the parser is the leading
  indicator that nutrition will fail next.
