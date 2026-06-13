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

# meal-planner CLI: prefer the repo venv, fall back to PATH.
MEAL_PLANNER="$REPO_ROOT/.venv/bin/meal-planner"
[[ -x "$MEAL_PLANNER" ]] || MEAL_PLANNER="meal-planner"

# Load .env (DB creds, ANTHROPIC_API_KEY, USDA key, …) then force the DB host to
# localhost: .env ships DB_HOST=postgres for in-container use, but this script
# runs on the host where Postgres is reached via the mapped port on localhost.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi
export DB_HOST="localhost"
export DB_PORT="${DB_PORT:-5432}"

# docker compose (v2 plugin) with a fallback to legacy docker-compose.
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

db_reachable() { "$MEAL_PLANNER" health >/dev/null 2>&1; }

ensure_postgres() {
  if db_reachable; then
    return 0
  fi
  log "Postgres not reachable on localhost:$DB_PORT — attempting to start it"
  if ! docker info >/dev/null 2>&1; then
    err "Docker isn't running. Start Docker Desktop (it hosts Postgres) and retry,"
    err "or point DB_HOST/DB_PORT at a Postgres you run yourself."
    exit 1
  fi
  ( cd "$REPO_ROOT" && compose up -d postgres )
  log "waiting for Postgres to accept connections…"
  for _ in $(seq 1 30); do
    if db_reachable; then
      log "Postgres is up"
      return 0
    fi
    sleep 1
  done
  err "Postgres did not become reachable in time"
  exit 1
}

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

ensure_postgres

log "applying any pending DB migrations"
( cd "$REPO_ROOT" && \
  { [[ -x ".venv/bin/alembic" ]] && .venv/bin/alembic upgrade head || alembic upgrade head; } )

log "running pipeline: meal-planner run"
( cd "$REPO_ROOT" && "$MEAL_PLANNER" run )

log "done — open the Streamlit dashboard to see the refreshed plan"
