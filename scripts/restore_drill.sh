#!/usr/bin/env bash
# TraceIQ restore drill — prove the latest backup actually restores.
#
# "Test a restore before you need one" (SELF_HOSTING.md) was advice with no
# tool behind it. This restores a pg_dump into a THROWAWAY database, brings it
# to the current schema exactly the way a real restore would (bootstrap_db.py),
# and checks what an auditor asks after a restore: does the schema match the
# code, is the data there, is the audit trail intact, are the artifacts there.
# It never touches the live `traceiq` database.
#
# Usage:
#   scripts/restore_drill.sh --dump backups/traceiq-2026-10-15.sql.gz
#   BACKUP_DIR=/srv/backups scripts/restore_drill.sh          # newest dump in the dir
#   scripts/restore_drill.sh --dump x.sql.gz --artifacts traceiq-artifacts-2026-10-15.tar.gz
#   scripts/restore_drill.sh --dump x.dump --compose          # own throwaway postgres
#
# Dump formats: plain (.sql), gzip'd plain (.sql.gz — what SELF_HOSTING.md's
# backup recipe writes) and custom (.dump / pg_dump -Fc). Artifacts: the MinIO
# data directory, or a .tar.gz of it (SELF_HOSTING.md recipe).
#
# Targets:
#   default    a scratch database (DRILL_DB, default traceiq_restore_drill)
#              inside the running postgres container (TRACEIQ_PG_CONTAINER,
#              default traceiq-postgres-1), dropped afterwards.
#   --compose  a separate postgres:15 in a throwaway compose project
#              (-p traceiq-drill), removed with its volume afterwards. Use this
#              on a production host so the drill shares nothing with prod.
#
# Checks run inside the backend image (TRACEIQ_BACKEND_IMAGE; defaults to the
# published image). From a repo checkout the backend/ source is mounted over it
# so the checks run the checkout's code (TRACEIQ_DRILL_MOUNT_SOURCE=0 to use
# the image's own code — what you want on a production host).
#
# Exit status: 0 = every check PASS (WARNs allowed), 1 = any FAIL, 2 = usage.
# --keep leaves the restored database for inspection.
set -euo pipefail
# Never `set -x` here: the database password is in this shell's variables.
umask 077

usage() { sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

DUMP=""; ARTIFACTS=""; COMPOSE=0; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="${2:-}"; shift 2 ;;
    --artifacts) ARTIFACTS="${2:-}"; shift 2 ;;
    --compose) COMPOSE=1; shift ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

if [ -z "$DUMP" ]; then
  [ -n "${BACKUP_DIR:-}" ] || { echo "give --dump FILE or set BACKUP_DIR" >&2; exit 2; }
  DUMP="$(ls -1t "$BACKUP_DIR"/*.sql "$BACKUP_DIR"/*.sql.gz "$BACKUP_DIR"/*.dump 2>/dev/null | head -1 || true)"
  [ -n "$DUMP" ] || { echo "no *.sql, *.sql.gz or *.dump in $BACKUP_DIR" >&2; exit 2; }
fi
[ -r "$DUMP" ] || { echo "cannot read dump $DUMP" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRILL_DB="${DRILL_DB:-traceiq_restore_drill}"
# Interpolated into SQL below, so restricted to a plain identifier.
[[ "$DRILL_DB" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
  echo "DRILL_DB must be a lowercase identifier ([a-z0-9_])" >&2; exit 2; }
case "$DRILL_DB" in traceiq|postgres|template0|template1)
  echo "refusing to use '$DRILL_DB' as the drill database" >&2; exit 2 ;; esac

IMAGE="${TRACEIQ_BACKEND_IMAGE:-}"
if [ -z "$IMAGE" ]; then
  if docker image inspect traceiq-backend-test:local >/dev/null 2>&1; then
    IMAGE=traceiq-backend-test:local
  else
    IMAGE="${TRACEIQ_REGISTRY:-ghcr.io/raja-9679}/traceiq-backend:${TRACEIQ_VERSION:-latest}"
  fi
fi
MOUNT=()
if [ "${TRACEIQ_DRILL_MOUNT_SOURCE:-1}" = "1" ] && [ -f "$REPO_ROOT/backend/scripts/bootstrap_db.py" ]; then
  MOUNT=(-v "$REPO_ROOT/backend":/src -w /src -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1)
fi

RESULTS=()
FAILED=0
record() {  # record STATUS "check" "detail"
  RESULTS+=("$(printf '%-5s %-30s %s' "$1" "$2" "$3")")
  [ "$1" = "FAIL" ] && FAILED=1
  return 0
}

# Anything echoed into the report passes through this: a failing check's last
# line can be a driver error quoting the connection string. Literal (not sed)
# replacement, because the password may contain regex metacharacters.
redact() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "${PGPASS_:-}" ]; then line="${line//"$PGPASS_"/***}"; fi
    printf '%s\n' "$line"
  done
}

# --- target database -------------------------------------------------------
if [ "$COMPOSE" = "1" ]; then
  PROJECT=traceiq-drill
  PGUSER_=drill; PGPASS_="$(openssl rand -hex 16)"
  export DRILL_PG_PASSWORD="$PGPASS_"
  docker compose -p "$PROJECT" -f - up -d --wait >/dev/null <<YAML
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: drill
      POSTGRES_PASSWORD: \${DRILL_PG_PASSWORD}
      POSTGRES_DB: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U drill"]
      interval: 2s
      retries: 30
YAML
  PGC="$(docker compose -p "$PROJECT" ps -q postgres)"
  PGHOST_="$(docker inspect "$PGC" -f '{{.Name}}' | sed 's#^/##')"
else
  PGC="${TRACEIQ_PG_CONTAINER:-traceiq-postgres-1}"
  docker inspect "$PGC" >/dev/null 2>&1 || {
    echo "postgres container '$PGC' not running (set TRACEIQ_PG_CONTAINER or use --compose)" >&2; exit 2; }
  PGUSER_="$(docker exec "$PGC" printenv POSTGRES_USER)"
  PGPASS_="$(docker exec "$PGC" printenv POSTGRES_PASSWORD)"
  PGHOST_="$PGC"
fi
NET="$(docker inspect "$PGC" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"

LOG=""
cleanup() {
  # The restore log can hold row data quoted in psql error DETAIL lines.
  if [ -n "$LOG" ]; then rm -f "$LOG"; fi
  if [ "$KEEP" = "1" ]; then
    echo "(--keep) restored database left in place: $DRILL_DB on $PGHOST_"
    return
  fi
  if [ "$COMPOSE" = "1" ]; then
    docker compose -p traceiq-drill -f - down -v >/dev/null 2>&1 <<YAML || true
services:
  postgres:
    image: postgres:15-alpine
YAML
  else
    docker exec "$PGC" psql -U "$PGUSER_" -d postgres -q \
      -c "DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE);" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

docker exec "$PGC" psql -U "$PGUSER_" -d postgres -q \
  -c "DROP DATABASE IF EXISTS ${DRILL_DB} WITH (FORCE);" \
  -c "CREATE DATABASE ${DRILL_DB};" >/dev/null

# --- restore ----------------------------------------------------------------
echo "Restoring $(basename "$DUMP") into ${DRILL_DB} on ${PGHOST_} ..."
LOG="$(mktemp)"
START=$(date +%s)
case "$DUMP" in
  *.sql.gz) gunzip -c "$DUMP" | docker exec -i "$PGC" psql -U "$PGUSER_" -d "$DRILL_DB" -q >"$LOG" 2>&1 || true ;;
  *.sql)    docker exec -i "$PGC" psql -U "$PGUSER_" -d "$DRILL_DB" -q <"$DUMP" >"$LOG" 2>&1 || true ;;
  *)        docker exec -i "$PGC" pg_restore -U "$PGUSER_" -d "$DRILL_DB" --no-owner --no-acl <"$DUMP" >"$LOG" 2>&1 || true ;;
esac
ELAPSED=$(( $(date +%s) - START ))
# A dump taken as a different role restores with "role ... does not exist" on
# its OWNER/GRANT statements; the data is still there, so those are tolerated.
ERRORS="$(grep -E '^(psql:.*)?ERROR|pg_restore: error' "$LOG" | grep -v 'role ".*" does not exist' || true)"
if [ -n "$ERRORS" ]; then
  record FAIL "restore" "$(echo "$ERRORS" | wc -l) error(s), first: $(echo "$ERRORS" | head -1 | redact | cut -c1-120)"
else
  record PASS "restore" "${ELAPSED}s, $(du -h "$DUMP" | cut -f1) dump"
fi
rm -f "$LOG"; LOG=""

# The database URL carries the password, so it is handed to `docker run` via
# the environment (`-e NAME` with no value copies it from the client's env)
# rather than on the command line, where `ps` on the host would show it to
# every local user for the duration of each check.
run_backend() {
  DATABASE_URL="postgresql+asyncpg://${PGUSER_}:${PGPASS_}@${PGHOST_}:5432/${DRILL_DB}" \
  docker run --rm -i --entrypoint "$1" --network "$NET" "${MOUNT[@]}" \
    -e DATABASE_URL \
    -e CELERY_BROKER_URL=redis://127.0.0.1:6379/0 \
    -e CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0 \
    -e MINIO_ENDPOINT=127.0.0.1:9 -e MINIO_ACCESS_KEY=drill -e MINIO_SECRET_KEY=drill \
    -e SECRET_KEY=restore-drill-not-a-real-secret \
    -e ENVIRONMENT=development \
    "$IMAGE" "${@:2}"
}

# --- schema -----------------------------------------------------------------
BEFORE="$(run_backend alembic current 2>/dev/null | grep -Eo '^[0-9a-f]{12}' | head -1 || true)"
HEAD="$(run_backend alembic heads 2>/dev/null | grep -Eo '^[0-9a-f]{12}' | head -1 || true)"
if [ -z "$BEFORE" ]; then
  record WARN "schema revision" "backup has no alembic_version (pre-migration backup?)"
elif [ "$BEFORE" = "$HEAD" ]; then
  record PASS "schema revision" "$BEFORE (= code head)"
else
  record WARN "schema revision" "$BEFORE, code head $HEAD — bootstrap will migrate it"
fi
if run_backend python scripts/bootstrap_db.py >/dev/null 2>&1; then
  record PASS "bootstrap_db (migrate)" "now at $(run_backend alembic current 2>/dev/null | grep -Eo '^[0-9a-f]{12}' | head -1)"
else
  record FAIL "bootstrap_db (migrate)" "scripts/bootstrap_db.py failed on the restored data"
fi
if run_backend alembic check >/dev/null 2>&1; then
  record PASS "alembic check" "schema matches the models"
else
  record FAIL "alembic check" "restored schema drifts from the models"
fi

# --- data + audit chain -------------------------------------------------------
SMOKE="$(run_backend python - 2>&1 <<'PY' || true
from sqlalchemy import create_engine, text
from app.core.config import db_url_for, settings
from app.services.audit import verify_chain

eng = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
with eng.connect() as c:
    counts = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
              for t in ("users", "workspace", "project", "testsuite", "testcase",
                        "testrun", "testcaseresult", "testschedule", "auditlog")}
    print("SMOKE_COUNTS " + " ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"SMOKE_USERS {counts['users']}")
    print(f"SMOKE_LATEST {c.execute(text('SELECT max(created_at) FROM testrun')).scalar()}")
    print("SMOKE_TRIGGER " + str(c.execute(text(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'auditlog'::regclass "
        "AND NOT tgisinternal")).scalar()))
    rows = [dict(r._mapping) for r in c.execute(text(
        "SELECT id, entity_type, entity_id, action, user_id, workspace_id, timestamp, "
        "changes, prev_hash, row_hash FROM auditlog ORDER BY id"))]
    ok, bad = verify_chain(rows)
    if ok:
        print(f"SMOKE_AUDIT ok {len(rows)} -")
    else:
        row = rows[bad]
        print(f"SMOKE_AUDIT {'unverifiable' if not row['row_hash'] else 'broken'} {len(rows)} {row['id']}")
PY
)"
smoke() { echo "$SMOKE" | sed -n "s/^SMOKE_$1 //p" | tail -1; }
if [ -z "$(smoke AUDIT)" ]; then
  record FAIL "smoke queries" "$(echo "$SMOKE" | grep -v "^\s*$" | tail -1 | redact | cut -c1-150)"
else
  if [ "$(smoke USERS)" = "0" ]; then
    record WARN "row counts" "$(smoke COUNTS) (no users — is this the right backup?)"
  else
    record PASS "row counts" "$(smoke COUNTS)"
  fi
  record PASS "latest run" "$(smoke LATEST)"
  if [ "$(smoke TRIGGER)" -ge 1 ]; then
    record PASS "audit append-only trigger" "present"
  else
    record FAIL "audit append-only trigger" "missing — the restored auditlog accepts UPDATE/DELETE"
  fi
  read -r A_STATE A_ROWS A_BAD <<<"$(smoke AUDIT)"
  case "$A_STATE" in
    ok) record PASS "audit chain" "$A_ROWS rows verified" ;;
    unverifiable) record WARN "audit chain" "rows from #$A_BAD back predate hash chaining" ;;
    *) record FAIL "audit chain" "broken at row #$A_BAD of $A_ROWS" ;;
  esac
fi

# --- artifacts ------------------------------------------------------------------
if [ -n "$ARTIFACTS" ]; then
  # MinIO stores one xl.meta per object; a plain copy of a bucket has files.
  if [ -d "$ARTIFACTS" ]; then
    OBJ="$(find "$ARTIFACTS" -name xl.meta | wc -l)"
    [ "$OBJ" -gt 0 ] || OBJ="$(find "$ARTIFACTS" -type f | wc -l)"
  elif [ -r "$ARTIFACTS" ]; then
    OBJ="$(tar tzf "$ARTIFACTS" 2>/dev/null | grep -c 'xl.meta$' || true)"
    [ "$OBJ" -gt 0 ] || OBJ="$(tar tzf "$ARTIFACTS" 2>/dev/null | grep -vc '/$' || true)"
  else
    OBJ=""
  fi
  if [ -z "$OBJ" ]; then
    record FAIL "artifact snapshot" "cannot read $ARTIFACTS"
  elif [ "$OBJ" = "0" ]; then
    record WARN "artifact snapshot" "0 objects in $(basename "$ARTIFACTS")"
  else
    record PASS "artifact snapshot" "$OBJ objects in $(basename "$ARTIFACTS")"
  fi
fi

# --- report ---------------------------------------------------------------------
echo
echo "TraceIQ restore drill — $(date -u +%FT%TZ)"
echo "dump: $DUMP"
echo "target: ${DRILL_DB} on ${PGHOST_}$([ "$COMPOSE" = "1" ] && echo ' (throwaway compose project traceiq-drill)')"
echo "image: $IMAGE$([ ${#MOUNT[@]} -gt 0 ] && echo ' + checkout source')"
echo "----------------------------------------------------------------------"
printf '%s\n' "${RESULTS[@]}"
echo "----------------------------------------------------------------------"
if [ "$FAILED" = "1" ]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
