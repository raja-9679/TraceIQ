#!/usr/bin/env bash
# Run tests that need a real Postgres, against a SCRATCH database.
#
# Several guarantees cannot be proven without a database — that federated SSO
# provisioning writes the right rows, that the audit trigger fires, that a
# migration round-trips. The unit suite (./run-tests.sh) has no database and CI
# has no Postgres service yet, so those tests live under tests/integration/ and
# skip unless TRACEIQ_LIVE_DB is set. This script sets it up and tears it down.
#
# It creates its own scratch database inside the already-running Postgres
# container, bootstraps the schema, runs pytest, and drops the database again.
# It never touches the `traceiq` database.
#
# Usage:
#   ./run-tests-live.sh                                  # all of tests/integration
#   ./run-tests-live.sh tests/integration/test_x.py -q   # one file
#   KEEP_DB=1 ./run-tests-live.sh ...                    # leave the scratch DB for psql
set -euo pipefail

IMAGE="${TRACEIQ_TEST_IMAGE:-traceiq-backend-test:local}"
PGC="${TRACEIQ_PG_CONTAINER:-traceiq-postgres-1}"
SCRATCH="${TRACEIQ_SCRATCH_DB:-traceiq_scratch_tests}"
REPO_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! docker inspect "$PGC" >/dev/null 2>&1; then
  echo "Postgres container '$PGC' is not running. Start the stack first:" >&2
  echo "  cd infrastructure && docker compose -f docker-compose.yml up -d postgres" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Test image '$IMAGE' missing — run ./run-tests.sh once to build it." >&2
  exit 1
fi

PGUSER_="$(docker exec "$PGC" printenv POSTGRES_USER)"
PGPASS_="$(docker exec "$PGC" printenv POSTGRES_PASSWORD)"
NET="$(docker inspect "$PGC" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"
URL="postgresql+asyncpg://${PGUSER_}:${PGPASS_}@${PGC}:5432/${SCRATCH}"

# Point the object store at the real MinIO with a SCRATCH bucket. Not for
# coverage — for speed: code that deletes artifacts (the purge, retention) hits
# an unreachable endpoint otherwise and boto3's retry/backoff turned a 10-second
# test file into two minutes. A reachable endpoint that answers NoSuchBucket
# fails immediately.
MINIOC="${TRACEIQ_MINIO_CONTAINER:-traceiq-minio-1}"
MINIO_EP=""; MINIO_AK="scratch"; MINIO_SK="scratch"
if docker inspect "$MINIOC" >/dev/null 2>&1; then
  MINIO_EP="${MINIOC}:9000"
  MINIO_AK="$(docker exec "$MINIOC" printenv MINIO_ROOT_USER 2>/dev/null || echo scratch)"
  MINIO_SK="$(docker exec "$MINIOC" printenv MINIO_ROOT_PASSWORD 2>/dev/null || echo scratch)"
else
  echo "note: '$MINIOC' not running — artifact-deletion paths will report errors."
  MINIO_EP="127.0.0.1:9000"
fi

cleanup() {
  [ -n "${KEEP_DB:-}" ] && { echo "Keeping scratch database '$SCRATCH'."; return; }
  docker exec "$PGC" psql -U "$PGUSER_" -d postgres -q \
    -c "DROP DATABASE IF EXISTS ${SCRATCH} WITH (FORCE);" >/dev/null
}
trap cleanup EXIT

docker exec "$PGC" psql -U "$PGUSER_" -d postgres -q \
  -c "DROP DATABASE IF EXISTS ${SCRATCH} WITH (FORCE);" \
  -c "CREATE DATABASE ${SCRATCH};" >/dev/null
echo "Scratch database ${SCRATCH} created."

run() {
  # --entrypoint bypasses the image's wait-for-database entrypoint.
  docker run --rm --entrypoint "$1" --network "$NET" \
    -v "$REPO_BACKEND":/src -w /src \
    -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 \
    -e DATABASE_URL="$URL" \
    -e CELERY_BROKER_URL=redis://localhost:6379/0 \
    -e CELERY_RESULT_BACKEND=redis://localhost:6379/0 \
    -e MINIO_ENDPOINT="$MINIO_EP" \
    -e MINIO_ACCESS_KEY="$MINIO_AK" -e MINIO_SECRET_KEY="$MINIO_SK" \
    -e MINIO_BUCKET_NAME="${TRACEIQ_SCRATCH_BUCKET:-traceiq-scratch-tests}" \
    -e SECRET_KEY=test-only-secret-value-not-real \
    -e TRACEIQ_LIVE_DB=1 \
    "$IMAGE" "${@:2}"
}

run python scripts/bootstrap_db.py >/dev/null
echo "Schema bootstrapped."

if [ $# -eq 0 ]; then
  set -- tests/integration -q
fi
run python -m pytest -p no:cacheprovider "$@"
