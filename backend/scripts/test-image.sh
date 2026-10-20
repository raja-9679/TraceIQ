#!/usr/bin/env bash
# Shared by run-tests.sh and run-tests-live.sh: resolve (and if needed build)
# the backend test image. Source it; it sets $IMAGE.
#
# The tag is derived from requirements.txt, so a dependency change (e.g. the
# 2026-10 FastAPI/Starlette bump) gets a NEW image automatically. Before this,
# the script reused whatever `traceiq-backend-test:local` existed — on a laptop
# with an older image the suite silently ran against the old dependencies, and
# a fresh laptop built it from the PUBLISHED image (`:latest`), which predates
# any unreleased dependency change.
#
# The base is THIS checkout's backend/Dockerfile (system libraries + exactly
# the pinned requirements.txt), not a published image: GHCR refuses anonymous
# pulls, and `:latest` would predate any unreleased dependency change anyway.
# The first build takes a few minutes; later runs reuse it.
# TRACEIQ_TEST_IMAGE still overrides everything.
_TI_BACKEND="${REPO_BACKEND:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [ -n "${TRACEIQ_TEST_IMAGE:-}" ]; then
  IMAGE="$TRACEIQ_TEST_IMAGE"
else
  _TI_HASH="$(sha256sum "$_TI_BACKEND/requirements.txt" | cut -c1-12)"
  IMAGE="traceiq-backend-test:req-$_TI_HASH"
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if [ -n "${TRACEIQ_TEST_IMAGE:-}" ]; then
    echo "Test image '$IMAGE' (TRACEIQ_TEST_IMAGE) does not exist." >&2
    exit 1
  fi
  echo "Building $IMAGE from requirements.txt (first run on this machine or after a dependency change) ..." >&2
  docker build -q -t "$IMAGE-base" --build-arg STRIP_SOURCE=0 \
    -f "$_TI_BACKEND/Dockerfile" "$_TI_BACKEND" >&2
  docker build -q -t "$IMAGE" --build-arg BASE="$IMAGE-base" -f - "$_TI_BACKEND" >&2 <<'DOCKERFILE'
ARG BASE
FROM ${BASE}
RUN pip install --no-cache-dir pytest==8.3.4 pytest-asyncio==0.25.2 pytest-cov==6.0.0
DOCKERFILE
fi
