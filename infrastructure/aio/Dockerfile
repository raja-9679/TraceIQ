# TraceIQ all-in-one (AIO) image — the "try it in 60 seconds" tier.
#
#   docker run -d --name traceiq --shm-size=1g \
#     -p 8080:8080 -p 9000:9000 -v traceiq:/data \
#     ghcr.io/raja-9679/traceiq-aio:latest
#
# One container bundling postgres + redis + minio + backend + celery ×3 +
# execution worker + nginx under supervisord. Secrets are generated on FIRST
# BOOT into the /data volume — nothing secret is baked into this image, which
# keeps the "no shared defaults across deployments" invariant intact.
#
# This is an evaluation tier, not the production path:
#   - execution workers cannot be scaled independently (one worker, chromium only)
#   - all state lives in a single /data volume
#   - one process supervisor is a single point of failure
# Production self-hosting uses docker-compose.community.yml instead.
#
# Build context is the REPO ROOT (it needs backend/, frontend/,
# execution-engine/). The root .dockerignore is deny-by-default; keep it that
# way — a root context would otherwise upload .git and dump.sql.
#
#   docker build -f infrastructure/aio/Dockerfile -t traceiq-aio .

# ---------------------------------------------------------------------------
# Stage 1: frontend build. VITE_API_BASE_URL is deliberately NOT set, so the
# bundle falls back to the relative /api that nginx proxies below.
# ---------------------------------------------------------------------------
FROM node:18-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: execution-engine build. Built on bookworm (not alpine) so the
# node_modules we copy into the final bookworm stage link against glibc.
# ---------------------------------------------------------------------------
FROM node:18-bookworm-slim AS engine-build
WORKDIR /build
COPY execution-engine/package*.json ./
RUN npm ci
COPY execution-engine/ .
# Minify after build for the same source-protection reasons as
# Dockerfile.worker: tsc output is otherwise as readable as the TypeScript.
RUN npm run build \
    && npm exec --yes esbuild@0.25.9 -- --version >/dev/null \
    && find dist -name '*.js' -exec npm exec --yes esbuild@0.25.9 -- {} --minify --platform=node --target=node18 --allow-overwrite --outfile={} \; \
    && npm prune --production

# ---------------------------------------------------------------------------
# Final stage. Pinned to -bookworm: the un-suffixed python:3.11-slim floated
# to Debian trixie, which drops postgresql-15 and renames several of the
# chromium runtime libs below.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONPATH=/opt/traceiq/backend \
    NODE_ENV=production \
    ARTIFACTS_DIR=/tmp/artifacts

# Bookworm ships postgresql 15 and redis 7, matching the compose stack.
# The lib* block is the Playwright chromium runtime (same list as
# backend/Dockerfile — playwright's own --with-deps references pre-bookworm
# package names).
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-15 postgresql-client-15 \
        redis-server \
        nginx \
        supervisor \
        nodejs \
        curl ca-certificates openssl \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 \
        libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libx11-6 libxcb1 \
        libxext6 fonts-liberation fonts-unifont \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# MinIO server binary (no distro package). Pinned to the SAME release the
# compose stack pins (minio/minio:RELEASE.2025-04-22T22-12-26Z) so the AIO and
# compose distributions ship identical MinIO versions — an unpinned `.../minio`
# floats to whatever "latest" is at build time. Verified against the checksum
# MinIO publishes alongside each archive (per-arch; amd64/arm64 differ).
ARG TARGETARCH=amd64
ARG MINIO_RELEASE=RELEASE.2025-04-22T22-12-26Z
RUN case "${TARGETARCH}" in \
      amd64) MINIO_SHA256=53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c ;; \
      arm64) MINIO_SHA256=6c2f3142c94240206123177f4ba1e360daa5d1e0a4962e90757ef4f92c3ab57c ;; \
      *) echo "No pinned MinIO checksum for arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://dl.min.io/server/minio/release/linux-${TARGETARCH}/archive/minio.${MINIO_RELEASE}" \
        -o /usr/local/bin/minio \
    && echo "${MINIO_SHA256}  /usr/local/bin/minio" | sha256sum -c - \
    && chmod +x /usr/local/bin/minio

# k6 for the load-test executor (same source as Dockerfile.worker).
COPY --from=grafana/k6:0.57.0 /usr/bin/k6 /usr/bin/k6

# Backend Python deps, then the python-playwright chromium build.
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && playwright install chromium \
    && rm /tmp/requirements.txt

# Application code.
COPY backend/ /opt/traceiq/backend/

# Same source protection as backend/Dockerfile: 3.11 bytecode only, keeping
# alembic (globbed as *.py by migrations) and scripts (run by path) as source.
ARG STRIP_SOURCE=1
RUN if [ "$STRIP_SOURCE" = "1" ]; then \
      cd /opt/traceiq/backend \
      && python -m compileall -q -b app \
      && find app -name '*.py' -not -path 'app/alembic/*' -delete \
      && find app -name '__pycache__' -type d -prune -exec rm -rf {} + ; \
    fi
COPY --from=engine-build /build/dist /opt/traceiq/engine/dist
COPY --from=engine-build /build/node_modules /opt/traceiq/engine/node_modules
COPY --from=engine-build /build/package.json /opt/traceiq/engine/package.json
COPY --from=frontend-build /build/dist /opt/traceiq/frontend/

# The node-playwright chromium build (1.40.x) is a different browser revision
# than the python one above; both live in /ms-playwright side by side.
RUN cd /opt/traceiq/engine && ./node_modules/.bin/playwright install chromium \
    && chmod -R a+rX /ms-playwright

# App processes run unprivileged, same uid as the distributed backend image.
# The worker can execute user-supplied scripts when RAW_PLAYWRIGHT_ENABLED is
# set, so root would hand those a root shell.
RUN groupadd --system --gid 10001 traceiq \
    && useradd --system --uid 10001 --gid traceiq --create-home traceiq \
    && chmod +x /opt/traceiq/backend/docker-entrypoint.sh

COPY infrastructure/aio/nginx.conf /etc/nginx/conf.d/traceiq.conf
COPY infrastructure/aio/supervisord.conf /etc/traceiq/supervisord.conf
COPY infrastructure/aio/run-service.sh infrastructure/aio/entrypoint.sh /opt/traceiq/bin/
RUN chmod +x /opt/traceiq/bin/run-service.sh /opt/traceiq/bin/entrypoint.sh

# All persistent state: postgres, redis AOF, minio objects, generated secrets.
VOLUME /data

# 8080 = UI + API (nginx). 9000 = MinIO; the browser fetches run artifacts
# (traces, videos) via presigned URLs, so it must be host-published too.
EXPOSE 8080 9000

HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=180s \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

ENTRYPOINT ["/opt/traceiq/bin/entrypoint.sh"]
