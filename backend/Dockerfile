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
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 \
    libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libx11-6 libxcb1 libxext6 \
    fonts-liberation fonts-unifont \
    && rm -rf /var/lib/apt/lists/* \
    && playwright install chromium

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
