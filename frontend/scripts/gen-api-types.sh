#!/usr/bin/env bash
# Regenerate src/api/generated/{openapi.json,schema.ts} from the backend.
#
#   npm run gen:api              # dump the backend OpenAPI + regenerate types
#   npm run gen:api -- --types   # only regenerate schema.ts from the committed JSON
#
# The OpenAPI document is dumped by importing the FastAPI app — no server, no
# database (backend/scripts/dump_openapi.py). It runs in the backend test image
# (TRACEIQ_TEST_IMAGE, default traceiq-backend-test:local — see
# backend/run-tests.sh) so the FastAPI/pydantic versions are the pinned ones: a
# different FastAPI renders a different document and the CI drift check fails.
# OPENAPI_PYTHON=/path/to/python uses a venv with backend/requirements.txt
# instead (that is what CI does). Output goes to stdout because the image's
# pip --user deps are only visible to its own user, which can't write the mount.
set -euo pipefail

FRONTEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$FRONTEND/../backend"
OUT="$FRONTEND/src/api/generated"
IMAGE="${TRACEIQ_TEST_IMAGE:-traceiq-backend-test:local}"

if [ "${1:-}" != "--types" ]; then
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  if [ -n "${OPENAPI_PYTHON:-}" ]; then
    (cd "$BACKEND" && "$OPENAPI_PYTHON" scripts/dump_openapi.py -) > "$tmp"
  else
    docker run --rm --entrypoint python -v "$(cd "$BACKEND" && pwd)":/src:ro -w /src \
      -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" scripts/dump_openapi.py - > "$tmp"
  fi
  mv "$tmp" "$OUT/openapi.json"
  trap - EXIT
fi

# --default-non-nullable false: a field with a default is OPTIONAL in the
# generated type — right for request bodies. Responses are read through
# schemaTypes.ts `Out<>`, which marks every field present (FastAPI serialises
# all of them).
# openapi-typescript is a pinned devDependency (npm ci installs it);
# OPENAPI_TYPESCRIPT=/path/to/openapi-typescript overrides the binary.
cd "$FRONTEND"
if [ -n "${OPENAPI_TYPESCRIPT:-}" ]; then
  "$OPENAPI_TYPESCRIPT" src/api/generated/openapi.json -o src/api/generated/schema.ts --default-non-nullable false
else
  npx --no-install openapi-typescript src/api/generated/openapi.json -o src/api/generated/schema.ts --default-non-nullable false
fi
