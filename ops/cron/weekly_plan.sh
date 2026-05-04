#!/usr/bin/env bash
# Weekly meal-planner cron entrypoint.
#
# Install:
#   crontab -e
#   0 6 * * 1 /path/to/repo/ops/cron/weekly_plan.sh >> /var/log/meal-planner.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export LOG_FORMAT="${LOG_FORMAT:-json}"
exec meal-planner run --config config/pipeline.yaml
