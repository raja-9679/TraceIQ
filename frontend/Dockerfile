# Build stage
# TODO(security): bump the Node base from 18 to 22 (18 is EOL). Deferred here
# because it needs a full build to verify the Vite build still passes.
FROM node:18-alpine as build

WORKDIR /app

COPY package*.json ./
RUN npm install

ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY . .
RUN npm run build

# Production stage
# Pinned to a minor tag rather than the floating `nginx:alpine`, so a rebuild
# is reproducible and doesn't silently jump nginx major versions.
FROM nginx:1.27-alpine

# Take Alpine's pending security updates (openssl et al.) at build time; the
# pinned tag lags the package index and Trivy showed fixable CRITICALs without it.
RUN apk upgrade --no-cache

COPY --from=build /app/dist /usr/share/nginx/html

# Where nginx proxies /api/ to, resolved at CONTAINER START (not build time):
# the base image's entrypoint envsubsts /etc/nginx/templates/*.template into
# conf.d/. Override with `-e BACKEND_URL=http://my-backend:8000` to point one
# prebuilt frontend image at a backend anywhere.
ENV BACKEND_URL=http://backend:8000
# DNS server nginx uses to re-resolve ${BACKEND_URL}'s hostname at request
# time (see nginx.conf). Left EMPTY here on purpose: docker-entrypoint.d/
# 16-traceiq-resolver.envsh fills it from the container's /etc/resolv.conf at
# start (Docker's 127.0.0.11 on compose, CoreDNS on Kubernetes). Set it
# explicitly only for a non-standard resolver.
ENV NGINX_RESOLVER=""
COPY docker-entrypoint.d/16-traceiq-resolver.envsh /docker-entrypoint.d/16-traceiq-resolver.envsh
# The base entrypoint silently IGNORES a non-executable script ("Ignoring …,
# not executable") — and then NGINX_RESOLVER is empty and nginx refuses to
# start. Do not rely on the checkout preserving the mode bit.
RUN chmod +x /docker-entrypoint.d/16-traceiq-resolver.envsh
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
