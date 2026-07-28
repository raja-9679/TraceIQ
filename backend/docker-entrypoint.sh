#!/usr/bin/env bash
# Container entrypoint for the TraceIQ backend image.
#
# Self-hosted deployments pull this image and run it — there is no operator to
# perform a manual migration step, so schema upgrades have to happen here. The
# previous CMD went straight to uvicorn, which meant a fresh `docker compose up`
# came up against an empty database and crashed on the first query.
#
# Set RUN_MIGRATIONS=false on replicas (Celery workers, extra API instances) so
# only one process migrates.
set -euo pipefail

log() { echo "[entrypoint] $*" >&2; }

RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"
MIGRATION_MAX_ATTEMPTS="${MIGRATION_MAX_ATTEMPTS:-30}"
MIGRATION_RETRY_DELAY="${MIGRATION_RETRY_DELAY:-2}"

if [ "${RUN_MIGRATIONS}" = "true" ]; then
  # Wait for the database separately from migrating, so a genuine schema error
  # fails immediately instead of being retried 30 times as if it were a
  # connection problem.
  log "Waiting for the database..."
  attempt=1
  until python -c "
import asyncio, sys
from sqlalchemy import text
from app.core.database import engine
async def ping():
    async with engine.connect() as conn:
        await conn.execute(text('SELECT 1'))
    await engine.dispose()
try:
    asyncio.run(ping())
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
    if [ "${attempt}" -ge "${MIGRATION_MAX_ATTEMPTS}" ]; then
      log "ERROR: database unreachable after ${attempt} attempts. Giving up."
      log "Check DATABASE_URL and that Postgres is running and accepting connections."
      exit 1
    fi
    log "Database not ready (attempt ${attempt}/${MIGRATION_MAX_ATTEMPTS}). Retrying in ${MIGRATION_RETRY_DELAY}s..."
    attempt=$((attempt + 1))
    sleep "${MIGRATION_RETRY_DELAY}"
  done
  log "Database reachable."

  # bootstrap_db.py, not `alembic upgrade head` directly: the Alembic baseline is
  # an empty stub, so migrations alone cannot build a schema from scratch. The
  # script creates it on an empty database and upgrades an existing one.
  log "Applying schema..."
  if ! python scripts/bootstrap_db.py; then
    log "ERROR: schema setup failed. See the output above."
    exit 1
  fi
else
  log "RUN_MIGRATIONS=${RUN_MIGRATIONS} — skipping schema setup."
fi

log "Starting: $*"
exec "$@"
