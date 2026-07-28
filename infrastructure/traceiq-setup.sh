#!/usr/bin/env bash
# First-run setup for a self-hosted TraceIQ instance.
#
# Generates the required secrets into .env so nobody has to invent them, then
# prints the next command. Safe to re-run: it will not overwrite an existing
# .env unless you pass --force.
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=".env"
TEMPLATE="env.community.example"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./traceiq-setup.sh [--force]

Creates .env from env.community.example with freshly generated secrets.
  --force   overwrite an existing .env (the old one is backed up)
USAGE
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: $TEMPLATE not found. Run this from the infrastructure/ directory." >&2
  exit 1
fi

if [ -f "$ENV_FILE" ] && [ "$FORCE" -ne 1 ]; then
  echo "$ENV_FILE already exists. Leaving it alone."
  echo "Re-run with --force to regenerate (this invalidates existing logins)."
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required to generate secrets." >&2
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  backup="${ENV_FILE}.backup.$(date +%Y%m%d%H%M%S)"
  cp "$ENV_FILE" "$backup"
  echo "Backed up existing $ENV_FILE to $backup"
fi

gen() { openssl rand -hex "${1:-32}"; }

SECRET_KEY="$(gen 32)"
WEBHOOK_SECRET="$(gen 32)"
POSTGRES_PASSWORD="$(gen 24)"
REDIS_PASSWORD="$(gen 24)"
MINIO_ROOT_PASSWORD="$(gen 24)"

cp "$TEMPLATE" "$ENV_FILE"

# Fill in only the blank required keys, leaving every comment and optional
# setting in place so the file stays self-documenting.
set_var() {
  local key="$1" val="$2"
  # The value is hex from openssl, so there are no sed metacharacters to escape.
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i.bak -E "s|^${key}=.*$|${key}=${val}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

set_var SECRET_KEY "$SECRET_KEY"
set_var WEBHOOK_SECRET "$WEBHOOK_SECRET"
set_var POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
set_var REDIS_PASSWORD "$REDIS_PASSWORD"
set_var MINIO_ROOT_PASSWORD "$MINIO_ROOT_PASSWORD"

chmod 600 "$ENV_FILE"

cat <<EOF

TraceIQ setup complete.

  Wrote $ENV_FILE with freshly generated secrets (mode 600).
  Distinct values were used for SECRET_KEY and WEBHOOK_SECRET so they can be
  rotated independently.

Before starting, review these in $ENV_FILE:

  MINIO_PUBLIC_URL       must be reachable by your BROWSER, not just by Docker.
                         Currently: http://localhost:9000
                         Change it if you serve TraceIQ to other machines.

  ALLOW_PRIVATE_NETWORK_TARGETS
                         Currently false, so tests against internal/private
                         addresses are refused. Most self-hosters need true —
                         read the note in $ENV_FILE first, because true also
                         lets any user of this instance probe your network.

Then start it:

  docker compose -f docker-compose.community.yml --env-file $ENV_FILE up -d

Watch it come up (the backend applies migrations on first boot):

  docker compose -f docker-compose.community.yml logs -f backend

Then open http://localhost:8080 and register the first account.

Keep $ENV_FILE backed up somewhere safe. Losing SECRET_KEY logs everyone out;
losing POSTGRES_PASSWORD or the MinIO credentials loses access to your data.
EOF
