#!/usr/bin/env bash
# Capture instance-admin guide pages (Worker fleet, Instance settings): hold the capture lock, make the docs
# account an instance admin, run the specs (same container as run.sh), and
# ALWAYS revoke — so no other writer's capture ever runs with the grant.
set -euo pipefail
# Local stack only: talks to the traceiq-postgres-1 / traceiq-frontend-1 containers.
#   e2e/docs-capture/admin-capture.sh pages/setup-worker-fleet pages/setup-instance-settings
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EMAIL="$(python3 -c "import json;print(json.load(open('$ROOT/e2e/.auth/docs-credentials.json'))['email'])")"
psql_() { docker exec traceiq-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$1\""; }
mkdir -p "$ROOT/e2e/.cache"
exec 9>"$ROOT/e2e/.cache/capture.lock"
echo "waiting for the capture lock ..." >&2
flock 9
trap 'psql_ "update users set is_instance_admin=false where email='"'"'$EMAIL'"'"'" >/dev/null; echo "instance admin revoked" >&2' EXIT
psql_ "update users set is_instance_admin=true where email='$EMAIL'" >/dev/null
NET=$(docker inspect traceiq-frontend-1 -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
PW=$(node -p "require('$ROOT/e2e/package.json').devDependencies['@playwright/test']")
docker run --rm --network "$NET" --ipc host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e TRACEIQ_DOCS_BASE_URL=http://frontend -e TRACEIQ_DOCS_VOICE=on \
  -e TRACEIQ_DOCS_VIDEO="${TRACEIQ_DOCS_VIDEO:-on}" \
  -v "$ROOT":/work -w /work/e2e "mcr.microsoft.com/playwright:v${PW}-noble" \
  sh -c 'npx playwright test -c docs-capture/playwright.config.ts "$@"' sh "$@"
rm -rf "$ROOT/frontend/public/docs/media/.tmp-video"
python3 "$ROOT/e2e/docs-capture/mux.py" "$ROOT/frontend/public/docs/media" "$ROOT/e2e"
python3 "$ROOT/e2e/docs-capture/export_md.py" "$ROOT" >/dev/null
