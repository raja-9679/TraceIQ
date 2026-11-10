#!/usr/bin/env bash
# Runs recorder.e2e.mjs in the same Playwright image the e2e suite uses, so the
# host needs only Docker. Uses e2e/node_modules for @playwright/test.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PW="$(node -p "require('$ROOT/e2e/package.json').devDependencies['@playwright/test']")"
[ -d "$ROOT/e2e/node_modules/@playwright/test" ] || (cd "$ROOT/e2e" && npm ci --no-audit --no-fund --silent)
# The script is copied next to e2e/node_modules so the import resolves.
docker run --rm --ipc host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e RECORDER_DIR=/work/integrations/browser-recorder \
  -v "$ROOT":/work -w /work/e2e "mcr.microsoft.com/playwright:v${PW}-noble" \
  sh -c 'cp "$RECORDER_DIR/test/recorder.e2e.mjs" ./.recorder.e2e.mjs; node ./.recorder.e2e.mjs; s=$?; rm -f ./.recorder.e2e.mjs; exit $s'
