FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright and others
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (used by the element-picker /api/inspect endpoint
# and the legacy in-process runner). Deps are installed manually because
# playwright 1.41's `--with-deps` references pre-bookworm package names
# (ttf-unifont) that no longer exist.
# Browsers go to a shared path rather than /root/.cache so the non-root runtime
# user below can still find them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# So `python scripts/foo.py` and pytest can import the `app` package. Without it
# only the script's own directory lands on sys.path.
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 \
    libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libx11-6 libxcb1 libxext6 \
    fonts-liberation fonts-unifont \
    && rm -rf /var/lib/apt/lists/* \
    && playwright install chromium \
    && chmod -R a+rX /ms-playwright

COPY . .

RUN chmod +x /app/docker-entrypoint.sh

# Run as an unprivileged user. This image is distributed for self-hosting, and
# the worker paths can execute user-supplied scripts when RAW_PLAYWRIGHT_ENABLED
# or ALLOW_PYTHON_SCRIPTS is set, so root would hand those a root shell.
RUN groupadd --system --gid 10001 traceiq \
    && useradd --system --uid 10001 --gid traceiq --create-home traceiq \
    && chown -R traceiq:traceiq /app
USER traceiq

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
