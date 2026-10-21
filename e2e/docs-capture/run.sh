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

# Narration: Piper (offline text-to-speech, MIT licence) and the US English
# "kristin" voice (public domain; trained from scratch on LibriVox, NOT
# fine-tuned from the research-only "lessac" voice like many Piper voices).
# Downloaded once into e2e/.cache/piper (gitignored), voice pinned by SHA-256;
# TRACEIQ_DOCS_VOICE=off skips it and records captions only.
# TRACEIQ_DOCS_VOICE_PACE=1.5 speaks slower (default 1.3, 1 = natural speed).
# TRACEIQ_DOCS_VIDEO_SPEED=1 keeps the recorded speed (default 1.25, applied by mux.py).
# TRACEIQ_DOCS_VIDEO=off takes the pictures only (quick runs while writing a page).
PIPER_DIR="$ROOT/e2e/.cache/piper"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
VOICE="en_US-kristin-medium"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/kristin/medium"
VOICE_SHA256="5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4"
if [ "${TRACEIQ_DOCS_VOICE:-on}" != "off" ] && [ ! -f "$PIPER_DIR/$VOICE.onnx" ]; then
  echo "Downloading the narration voice (Piper + $VOICE, ~130 MB, once) ..." >&2
  mkdir -p "$PIPER_DIR"
  [ -x "$PIPER_DIR/piper/piper" ] || curl -fsSL "$PIPER_URL" | tar xz -C "$PIPER_DIR"
  curl -fsSL -o "$PIPER_DIR/$VOICE.onnx.json" "$VOICE_URL/$VOICE.onnx.json"
  curl -fsSL -o "$PIPER_DIR/$VOICE.onnx.tmp" "$VOICE_URL/$VOICE.onnx"
  echo "$VOICE_SHA256  $PIPER_DIR/$VOICE.onnx.tmp" | sha256sum -c --quiet -
  mv "$PIPER_DIR/$VOICE.onnx.tmp" "$PIPER_DIR/$VOICE.onnx"
fi

# One capture at a time: runs share e2e/node_modules (npm ci), the login state
# in e2e/.auth, the .tmp-video folder and mux.py's sweep of every *.webm, so two
# at once would break each other. A second run waits here for the first.
mkdir -p "$ROOT/e2e/.cache"
exec 9>"$ROOT/e2e/.cache/capture.lock"
if ! flock -n 9; then
  echo "Another capture is running; waiting for it to finish ..." >&2
  flock 9
fi

if [ -z "$NET" ]; then
  echo "Could not find the stack network (is $FRONTEND_C running?)." >&2
  exit 1
fi

docker run --rm --network "$NET" --ipc host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e TRACEIQ_DOCS_BASE_URL="${TRACEIQ_DOCS_BASE_URL:-http://frontend}" \
  -e TRACEIQ_DOCS_VOICE="${TRACEIQ_DOCS_VOICE:-on}" \
  -e TRACEIQ_DOCS_VOICE_PACE="${TRACEIQ_DOCS_VOICE_PACE:-}" \
  -e TRACEIQ_DOCS_VIDEO="${TRACEIQ_DOCS_VIDEO:-on}" \
  -v "$ROOT":/work -w /work/e2e \
  "mcr.microsoft.com/playwright:v${PW_VERSION}-noble" \
  sh -c '{ cmp -s package-lock.json node_modules/.traceiq-lock || { npm ci --no-audit --no-fund --silent && cp package-lock.json node_modules/.traceiq-lock; }; } && npx playwright test -c docs-capture/playwright.config.ts "$@"' sh "$@"

rm -rf "$ROOT/frontend/public/docs/media/.tmp-video"

# Playwright records WebM (VP8), which Safari plays only partly: mux.py turns
# each one into an H.264 MP4 with the spoken captions mixed in. Without ffmpeg
# the WebM stays (the guide's <video> lists both sources) and is silent.
if command -v ffmpeg >/dev/null 2>&1; then
  python3 "$ROOT/e2e/docs-capture/mux.py" "$ROOT/frontend/public/docs/media" "$ROOT/e2e"
else
  echo "ffmpeg not found: videos stay WebM, without voice (install ffmpeg and re-run)." >&2
fi
# A Markdown copy of every guide page (with its video script), next to the HTML.
python3 "$ROOT/e2e/docs-capture/export_md.py" "$ROOT" >/dev/null
echo "Media written under frontend/public/docs/media/; Markdown copies refreshed."
