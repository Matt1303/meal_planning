# Runbook: ingest finds 0 HTML files

## Symptoms
- `meal-planner ingest` exits with `recipe path not found` or
  `files=0 recipes=0`.
- `pipeline_metric.ingest_recipes` is 0.

## Triage
1. Check the configured path exists:
   ```bash
   meal-planner config print | grep path
   ls -la "$(meal-planner config print --format json | jq -r .sources.local_html.path)"
   ```
2. Check selectors still match an example file:
   ```bash
   meal-planner ingest 2>&1 | grep selectors_unmatched
   ```

## Recovery
- If the path has moved, update `config/pipeline.yaml#sources.local_html.path`.
- If the HTML structure changed (e.g., the producer of the HTML files
  rebranded their export), update the selectors. The ingest module logs
  unmatched selectors at warning level.

## Prevention
- The selector validation step in ingest emits per-selector
  `ingest_selector_unmatched_*` metrics. Alert on any non-zero value
  immediately after a run.
