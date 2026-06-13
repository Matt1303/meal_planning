#!/usr/bin/env bash
#
# paprika_refresh.sh — sync the newest Paprika HTML export into the pipeline
# and run the full meal-planner refresh.
#
# Manual steps before running this:
#   1. Edit recipes in the Paprika app.
#   2. Select all (Cmd-A) -> Export -> HTML -> save into the export dir
#      (default: ~/MealPlanAutomation).
#
# Then run:  ./scripts/paprika_refresh.sh
#        or: make paprika-refresh
#
# What it does:
#   1. Finds the most-recently-modified export folder under the export dir
#      (each Paprika export creates a folder like "190 recipes/Recipes/...").
#   2. rsyncs that export's Recipes/ + index.html into recipes_html/, deleting
#      recipes you removed in Paprika so the DB stays in step.
#   3. Runs `meal-planner run` (ingest -> parse -> enrich -> optimise -> report).
#
# Env overrides:
#   PAPRIKA_EXPORT_DIR   where Paprika exports land   (default ~/MealPlanAutomation)
#   RECIPES_HTML_DIR     pipeline source dir          (default <repo>/recipes_html)
#   SKIP_PIPELINE=1      sync only, don't run the pipeline (for testing)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPORT_DIR="${PAPRIKA_EXPORT_DIR:-$HOME/MealPlanAutomation}"
TARGET_DIR="${RECIPES_HTML_DIR:-$REPO_ROOT/recipes_html}"

log() { printf '\033[1;34m[paprika-refresh]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[paprika-refresh] ERROR:\033[0m %s\n' "$*" >&2; }

if [[ ! -d "$EXPORT_DIR" ]]; then
  err "export dir not found: $EXPORT_DIR"
  err "Set PAPRIKA_EXPORT_DIR or export recipes from Paprika into that folder."
  exit 1
fi

# A valid Paprika export folder contains a Recipes/ subfolder with .html files.
# Pick the newest such folder by modification time.
newest_export=""
newest_mtime=0
while IFS= read -r -d '' recipes_subdir; do
  export_root="$(dirname "$recipes_subdir")"
  if compgen -G "$recipes_subdir/*.html" > /dev/null; then
    mtime=$(stat -f %m "$export_root" 2>/dev/null || stat -c %Y "$export_root")
    if (( mtime > newest_mtime )); then
      newest_mtime=$mtime
      newest_export="$export_root"
    fi
  fi
done < <(find "$EXPORT_DIR" -type d -name Recipes -print0)

if [[ -z "$newest_export" ]]; then
  err "no Paprika export with a Recipes/ subfolder found under $EXPORT_DIR"
  err "In Paprika: select all -> Export -> HTML -> save into $EXPORT_DIR"
  exit 1
fi

recipe_count=$(find "$newest_export/Recipes" -maxdepth 1 -name '*.html' | wc -l | tr -d ' ')
log "newest export: $newest_export ($recipe_count recipes)"
log "syncing into:  $TARGET_DIR"

mkdir -p "$TARGET_DIR/Recipes"

# --delete keeps removed recipes from lingering; exclude macOS cruft.
rsync -a --delete \
  --exclude '.DS_Store' \
  "$newest_export/Recipes/" "$TARGET_DIR/Recipes/"

if [[ -f "$newest_export/index.html" ]]; then
  cp "$newest_export/index.html" "$TARGET_DIR/index.html"
fi

synced_count=$(find "$TARGET_DIR/Recipes" -maxdepth 1 -name '*.html' | wc -l | tr -d ' ')
log "synced $synced_count recipe files"

if [[ "${SKIP_PIPELINE:-0}" == "1" ]]; then
  log "SKIP_PIPELINE=1 set — stopping before pipeline run"
  exit 0
fi

log "running pipeline: meal-planner run"
cd "$REPO_ROOT"
if [[ -x ".venv/bin/meal-planner" ]]; then
  .venv/bin/meal-planner run
else
  meal-planner run
fi

log "done — open the Streamlit dashboard to see the refreshed plan"
