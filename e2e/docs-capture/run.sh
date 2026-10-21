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

# Narration: Piper (offline text-to-speech, MIT licence) and the LJSpeech
# voice (public domain). Downloaded once into e2e/.cache/piper (gitignored);
# TRACEIQ_DOCS_VOICE=off skips it and records captions only.
PIPER_DIR="$ROOT/e2e/.cache/piper"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high"
VOICE_SHA256="5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a"
if [ "${TRACEIQ_DOCS_VOICE:-on}" != "off" ] && [ ! -f "$PIPER_DIR/en_US-ljspeech-high.onnx" ]; then
  echo "Downloading the narration voice (Piper + LJSpeech, ~130 MB, once) ..." >&2
  mkdir -p "$PIPER_DIR"
  curl -fsSL "$PIPER_URL" | tar xz -C "$PIPER_DIR"
  curl -fsSL -o "$PIPER_DIR/en_US-ljspeech-high.onnx.json" "$VOICE_URL/en_US-ljspeech-high.onnx.json"
  curl -fsSL -o "$PIPER_DIR/en_US-ljspeech-high.onnx.tmp" "$VOICE_URL/en_US-ljspeech-high.onnx"
  echo "$VOICE_SHA256  $PIPER_DIR/en_US-ljspeech-high.onnx.tmp" | sha256sum -c --quiet -
  mv "$PIPER_DIR/en_US-ljspeech-high.onnx.tmp" "$PIPER_DIR/en_US-ljspeech-high.onnx"
fi

if [ -z "$NET" ]; then
  echo "Could not find the stack network (is $FRONTEND_C running?)." >&2
  exit 1
fi

docker run --rm --network "$NET" --ipc host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e TRACEIQ_DOCS_BASE_URL="${TRACEIQ_DOCS_BASE_URL:-http://frontend}" \
  -e TRACEIQ_DOCS_VOICE="${TRACEIQ_DOCS_VOICE:-on}" \
  -v "$ROOT":/work -w /work/e2e \
  "mcr.microsoft.com/playwright:v${PW_VERSION}-noble" \
  sh -c 'npm ci --no-audit --no-fund --silent && npx playwright test -c docs-capture/playwright.config.ts "$@"' sh "$@"

rm -rf "$ROOT/frontend/public/docs/media/.tmp-video"

# Playwright records WebM (VP8), which Safari plays only partly: mux.py turns
# each one into an H.264 MP4 with the spoken captions mixed in. Without ffmpeg
# the WebM stays (the guide's <video> lists both sources) and is silent.
if command -v ffmpeg >/dev/null 2>&1; then
  python3 "$ROOT/e2e/docs-capture/mux.py" "$ROOT/frontend/public/docs/media" "$ROOT/e2e"
else
  echo "ffmpeg not found: videos stay WebM, without voice (install ffmpeg and re-run)." >&2
fi
echo "Media written under frontend/public/docs/media/"
