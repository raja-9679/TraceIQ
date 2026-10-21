#!/usr/bin/env bash
# Render a guide page from disk (no frontend rebuild needed) and report console
# errors, broken pictures, missing alt text, the menu entry, the pager and any
# horizontal scrolling (desktop and phone width).
#   e2e/docs-capture/check-page.sh today/overview [screenshot.png]
# The screenshot path is inside the repo or an absolute path under /tmp.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SLUG="$1"; SHOT="${2:-}"
[ -f "$ROOT/frontend/public/docs/$SLUG.html" ] || { echo "no page frontend/public/docs/$SLUG.html" >&2; exit 1; }
PW_VERSION="$(node -p "require('$ROOT/e2e/package.json').devDependencies['@playwright/test']" 2>/dev/null)"
OUT_DIR=""; OUT_ARG=""
if [ -n "$SHOT" ]; then OUT_DIR="$(cd "$(dirname "$SHOT")" && pwd)"; OUT_ARG="/out/$(basename "$SHOT")"; fi
# Same lock as run.sh: that one runs `npm ci`, which empties node_modules.
mkdir -p "$ROOT/e2e/.cache"
exec 9>"$ROOT/e2e/.cache/capture.lock"
flock 9
[ -d "$ROOT/e2e/node_modules/@playwright/test" ] || (cd "$ROOT/e2e" && npm ci --no-audit --no-fund --silent)
docker run --rm --ipc host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$ROOT":/work ${OUT_DIR:+-v "$OUT_DIR":/out} -w /work/e2e \
  "mcr.microsoft.com/playwright:v${PW_VERSION}-noble" \
  node docs-capture/check-page.mjs "$SLUG" $OUT_ARG
