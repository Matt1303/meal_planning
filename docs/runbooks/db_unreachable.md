# Runbook: db unreachable

## Symptoms
- `meal-planner run` exits with `database not reachable`.
- `meal-planner db check` prints `db: unreachable`.
- `/healthz` returns 503.

## Triage

1. Is Postgres up?
   ```bash
   docker-compose ps postgres
   docker-compose logs --tail=100 postgres
   ```
2. Are the env vars right?
   ```bash
   meal-planner config print | head -40
   echo "DB_HOST=$DB_HOST DB_PORT=$DB_PORT DB_NAME=$DB_NAME"
   ```
3. Can you connect manually?
   ```bash
   psql "postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" -c 'select 1'
   ```

## Recovery
- If Postgres is down, restart: `docker-compose up -d postgres`.
- If creds rotated, update `.env` and restart the worker.
- If migrations are missing, run `make migrate`.

## Prevention
The cron wrapper sources `.env` before running, so credential rotation that
forgets to update `.env` is the most common cause. Add monitoring on
`pipeline_metric` "no rows in last 24h" to catch silent failure modes.
