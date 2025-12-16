FROM node:18-slim

WORKDIR /app

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./
RUN npm install

# Install Playwright browsers
RUN npx playwright install --with-deps chromium

COPY . .
RUN npm run build

CMD ["npm", "start"]
