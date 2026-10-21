#!/usr/bin/env bash
# Refresh the user guide's screenshots and recordings (frontend/public/docs/media)
# from a RUNNING TraceIQ stack.
#
#   e2e/docs-capture/run.sh                         # every page
#   e2e/docs-capture/run.sh pages/today-overview    # one page (Playwright filter)
#
# The browser runs in the pinned Playwright image ON the stack's Docker
# network and talks to the `frontend` service directly, so nothing has to be
# published on the host. Override with:
#   TRACEIQ_DOCS_NETWORK   docker network of the stack (default: the network
#                          the frontend container is on)
#   TRACEIQ_DOCS_FRONTEND  frontend container name (default traceiq-frontend-1)
#   TRACEIQ_DOCS_BASE_URL  URL of the app from inside that network (default http://frontend)
#
# First run signs up a "docs" account and gives it the demo workspace; its
# credentials stay in e2e/.auth/ (gitignored) and are reused afterwards.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_C="${TRACEIQ_DOCS_FRONTEND:-traceiq-frontend-1}"
NET="${TRACEIQ_DOCS_NETWORK:-$(docker inspect "$FRONTEND_C" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')}"
PW_VERSION="$(node -p "require('$ROOT/e2e/package.json').devDependencies['@playwright/test']" 2>/dev/null \
  || grep -o '"@playwright/test": *"[^"]*"' "$ROOT/e2e/package.json" | grep -o '[0-9][0-9.]*')"

if [ -z "$NET" ]; then
  echo "Could not find the stack network (is $FRONTEND_C running?)." >&2
  exit 1
fi

docker run --rm --network "$NET" --ipc host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e TRACEIQ_DOCS_BASE_URL="${TRACEIQ_DOCS_BASE_URL:-http://frontend}" \
  -v "$ROOT":/work -w /work/e2e \
  "mcr.microsoft.com/playwright:v${PW_VERSION}-noble" \
  sh -c 'npm ci --no-audit --no-fund --silent && npx playwright test -c docs-capture/playwright.config.ts "$@"' sh "$@"

rm -rf "$ROOT/frontend/public/docs/media/.tmp-video"

# Playwright records WebM (VP8), which Safari plays only partly, so each video
# is converted to H.264 MP4. Without ffmpeg the WebM stays (the guide's
# <video> lists both sources).
if command -v ffmpeg >/dev/null 2>&1; then
  find "$ROOT/frontend/public/docs/media" -name '*.webm' -newer "$ROOT/e2e/package.json" -print0 |
    while IFS= read -r -d '' v; do
      # Keep only the MP4 (plays everywhere) so the repo doesn't carry every video twice.
      ffmpeg -v error -y -i "$v" -c:v libx264 -pix_fmt yuv420p -crf 26 -preset slow \
        -movflags +faststart -an "${v%.webm}.mp4" </dev/null && rm -f "$v"
    done
else
  echo "ffmpeg not found: videos are WebM only (install ffmpeg and re-run for MP4)." >&2
fi
echo "Media written under frontend/public/docs/media/"
