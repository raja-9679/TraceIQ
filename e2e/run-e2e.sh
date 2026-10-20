#!/usr/bin/env bash
# Run TraceIQ's own E2E suite (WP15c) against a throwaway community stack.
#
#   e2e/run-e2e.sh --build          # build :e2e images from this checkout, run, tear down
#   e2e/run-e2e.sh                  # reuse already-built/pulled images
#   e2e/run-e2e.sh --keep           # leave the stack up afterwards (debugging)
#   e2e/run-e2e.sh -- --grep Runs   # anything after -- goes to `playwright test`
#
# Environment:
#   TRACEIQ_VERSION    image tag to run (default: e2e)
#   TRACEIQ_REGISTRY   image registry   (default: ghcr.io/raja-9679)
#   E2E_PROJECT        compose project  (default: traceiq-e2e)
#
# The stack gets its own compose project, volumes, network and generated .env,
# publishes nothing on the host, and the Playwright runner joins its network —
# so it never touches another TraceIQ stack on the same machine. Report:
# e2e/playwright-report/ (HTML) and e2e/test-results/ (traces, screenshots,
# junit.xml, stack logs on failure).
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TRACEIQ_VERSION="${TRACEIQ_VERSION:-e2e}"
export TRACEIQ_REGISTRY="${TRACEIQ_REGISTRY:-ghcr.io/raja-9679}"
PROJECT="${E2E_PROJECT:-traceiq-e2e}"
BUILD=0
KEEP=0
PW_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --build) BUILD=1 ;;
    --keep) KEEP=1 ;;
    --) shift; PW_ARGS=("$@"); break ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$BUILD" -eq 1 ]; then
  echo "==> Building images :${TRACEIQ_VERSION}"
  docker build -t "${TRACEIQ_REGISTRY}/traceiq-backend:${TRACEIQ_VERSION}" "$ROOT/backend"
  docker build -t "${TRACEIQ_REGISTRY}/traceiq-frontend:${TRACEIQ_VERSION}" "$ROOT/frontend"
  docker build -t "${TRACEIQ_REGISTRY}/traceiq-execution-worker:${TRACEIQ_VERSION}" \
    -f "$ROOT/execution-engine/Dockerfile.worker" "$ROOT/execution-engine"
fi

# Generate a .env with the real setup script (fresh secrets every run), in a
# private temp dir so an operator's infrastructure/.env is never touched.
WORK="$(mktemp -d)"
cp "$ROOT/infrastructure/traceiq-setup.sh" "$ROOT/infrastructure/env.community.example" "$WORK/"
"$WORK/traceiq-setup.sh" >/dev/null
ENV_FILE="$WORK/.env"
# Override keys in place: a duplicate key in an env file is resolved
# differently by different tools, so never rely on "last one wins".
set_env() {
  grep -v -E "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
}
set_env TRACEIQ_VERSION "$TRACEIQ_VERSION"
set_env TRACEIQ_REGISTRY "$TRACEIQ_REGISTRY"
# The browser under test runs inside the stack network, so it reaches the app
# and presigned artifact URLs by service name.
set_env FRONTEND_BASE_URL "http://frontend"
set_env BACKEND_CORS_ORIGINS '["http://frontend"]'
set_env MINIO_PUBLIC_URL "http://object-store:9000"
# The test cases browse e2e-target, a container on the stack's own network.
set_env ALLOW_PRIVATE_NETWORK_TARGETS true
set_env EXECUTION_WORKERS 2

export E2E_UID="$(id -u)" E2E_GID="$(id -g)"
COMPOSE=(docker compose -p "$PROJECT" --env-file "$ENV_FILE"
  -f "$ROOT/infrastructure/docker-compose.community.yml" -f "$ROOT/e2e/docker-compose.e2e.yml")

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    mkdir -p "$ROOT/e2e/test-results"
    "${COMPOSE[@]}" logs --no-color --timestamps > "$ROOT/e2e/test-results/stack-logs.txt" 2>&1 || true
    echo "Stack logs: e2e/test-results/stack-logs.txt"
  fi
  if [ "$KEEP" -eq 1 ]; then
    echo "Stack left running (project $PROJECT). Tear down with:"
    echo "  ${COMPOSE[*]} down -v"
    echo "(env file: $ENV_FILE)"
  else
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK"
  fi
  exit "$status"
}
trap cleanup EXIT

echo "==> Starting stack ($PROJECT)"
# --wait blocks until every service with a healthcheck is healthy.
"${COMPOSE[@]}" up -d --wait --wait-timeout 300

echo "==> Running Playwright"
start=$(date +%s)
if [ ${#PW_ARGS[@]} -gt 0 ]; then
  "${COMPOSE[@]}" run --rm e2e sh -c 'npm ci --no-audit --no-fund && npx playwright test "$@"' sh "${PW_ARGS[@]}"
else
  "${COMPOSE[@]}" run --rm e2e
fi
echo "==> E2E passed in $(( $(date +%s) - start ))s"
