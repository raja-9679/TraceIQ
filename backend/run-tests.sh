#!/usr/bin/env bash
# Run the backend unit suite against Python 3.11 (what CI and the image use).
#
# The host may have a newer Python — psycopg2-binary ships no wheel for 3.12+
# and building it needs pg_config, so a host venv is not a reliable path. This
# borrows the backend image (which already has every runtime dep at the right
# version), adds pytest, and mounts the working tree over it.
#
# Build the helper image once:
#   docker build -t traceiq-backend-test:local -f - . <<'EOF'
#   FROM ghcr.io/raja-9679/traceiq-backend:latest
#   RUN pip install --no-cache-dir pytest==8.3.4 pytest-asyncio==0.25.2 pytest-cov==6.0.0
#   EOF
#
# Usage: ./run-tests.sh [pytest args...]   (defaults to the unit suite)
set -euo pipefail

IMAGE="${TRACEIQ_TEST_IMAGE:-traceiq-backend-test:local}"
REPO_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE ..."
  docker build -q -t "$IMAGE" -f - "$REPO_BACKEND" <<'DOCKERFILE'
FROM ghcr.io/raja-9679/traceiq-backend:latest
RUN pip install --no-cache-dir pytest==8.3.4 pytest-asyncio==0.25.2 pytest-cov==6.0.0
DOCKERFILE
fi

# Default target: everything at tests/ root except the ad-hoc verify_* scripts,
# which are manual tools rather than pytest modules. tests/e2e and
# tests/integration need a live stack and are excluded here.
if [ $# -eq 0 ]; then
  set -- tests/ --ignore=tests/e2e --ignore=tests/integration \
    --ignore-glob='tests/verify_*.py' -q
fi

# --entrypoint python bypasses the image's wait-for-database entrypoint.
exec docker run --rm \
  --entrypoint python \
  -v "$REPO_BACKEND":/src \
  -w /src \
  -e PYTHONPATH=/src \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" -m pytest -p no:cacheprovider "$@"
