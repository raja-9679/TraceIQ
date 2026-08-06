#!/usr/bin/env bash
# First-run setup for a self-hosted TraceIQ instance.
#
# Writes .env with the required secrets so nobody has to invent them. Every
# value is customizable BEFORE first start — precedence, highest first:
#
#   1. A value already set in the environment when this script runs:
#        POSTGRES_PASSWORD='my-own-password' FRONTEND_PORT=9090 ./traceiq-setup.sh
#   2. Answers given in --interactive mode (Enter accepts the default)
#   3. Auto-generated (secrets) or the template default (everything else)
#
# Safe to re-run: it will not overwrite an existing .env unless you pass
# --force.
set -euo pipefail

# Restrict the permissions of every file this script creates (.env, its backup,
# and the .tmp splice file) so none is ever world-/group-readable during the
# write window — the final `chmod 600 .env` only fixes .env, and only after the
# fact. Set before anything touches the filesystem.
umask 077

cd "$(dirname "$0")"

ENV_FILE=".env"
TEMPLATE="env.community.example"
FORCE=0
INTERACTIVE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --interactive) INTERACTIVE=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./traceiq-setup.sh [--force] [--interactive]

Creates .env from env.community.example. Secrets are auto-generated unless you
supply your own, either as environment variables:

  POSTGRES_PASSWORD='my-own-password' ./traceiq-setup.sh

or interactively:

  ./traceiq-setup.sh --interactive

  --interactive   prompt for each secret and common setting (Enter = default)
  --force         overwrite an existing .env (the old one is backed up)
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

SECRETS="SECRET_KEY WEBHOOK_SECRET POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD"
TUNABLES="FRONTEND_PORT BACKEND_PORT MINIO_PORT MINIO_PUBLIC_URL ALLOW_PRIVATE_NETWORK_TARGETS EXECUTION_WORKERS"

# Track provenance for the summary: user | prompt | generated | template
declare -A SOURCE VALUE

default_for() {
  case "$1" in
    SECRET_KEY|WEBHOOK_SECRET) gen 32 ;;
    POSTGRES_PASSWORD|REDIS_PASSWORD|MINIO_ROOT_PASSWORD) gen 24 ;;
    MINIO_ROOT_USER) echo "traceiq-$(gen 4)" ;;
    # Tunables: whatever the template currently says.
    *) grep -E "^$1=" "$TEMPLATE" | head -1 | cut -d= -f2- ;;
  esac
}

is_secret() { case " $SECRETS " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# 1. Environment wins.
for key in $SECRETS $TUNABLES; do
  val="${!key:-}"
  if [ -n "$val" ]; then
    VALUE[$key]="$val"; SOURCE[$key]="user"
  fi
done

# 2. Interactive prompts for anything not already supplied. Reads /dev/tty so
#    the wizard also works when the script itself arrives on stdin (curl|bash).
if [ "$INTERACTIVE" -eq 1 ]; then
  # The /dev/tty node exists even without a controlling terminal (where opening
  # it fails), so test by actually opening it.
  if (exec </dev/tty >/dev/tty) 2>/dev/null; then
    echo "Interactive setup — press Enter to accept the default." >/dev/tty
    for key in $SECRETS $TUNABLES; do
      [ -n "${SOURCE[$key]:-}" ] && continue
      if is_secret "$key"; then
        printf '  %s [auto-generate]: ' "$key" >/dev/tty
        IFS= read -rs answer </dev/tty
        printf '\n' >/dev/tty
      else
        tmpl_default="$(default_for "$key")"
        printf '  %s [%s]: ' "$key" "${tmpl_default:-empty}" >/dev/tty
        IFS= read -r answer </dev/tty
      fi
      if [ -n "$answer" ]; then
        VALUE[$key]="$answer"; SOURCE[$key]="prompt"
      fi
    done
  else
    echo "NOTE: --interactive requested but no terminal is available; using defaults."
    echo "      Supply values as environment variables instead (see --help)."
  fi
fi

# 3. Generate / template-default the rest.
for key in $SECRETS $TUNABLES; do
  [ -n "${SOURCE[$key]:-}" ] && continue
  VALUE[$key]="$(default_for "$key")"
  if is_secret "$key"; then SOURCE[$key]="generated"; else SOURCE[$key]="template"; fi
done

# Validate user-supplied values BEFORE writing anything, mirroring the
# backend's validate_for_deployment() so a bad value fails here with a clear
# message instead of at first boot.
fail=0
for key in SECRET_KEY WEBHOOK_SECRET; do
  if [ "${SOURCE[$key]}" != "generated" ] && [ "${#VALUE[$key]}" -lt 32 ]; then
    echo "ERROR: $key is only ${#VALUE[$key]} characters; the backend refuses" >&2
    echo "       anything under 32. Generate one with: openssl rand -hex 32" >&2
    fail=1
  fi
done
if [ "${VALUE[SECRET_KEY]}" = "${VALUE[WEBHOOK_SECRET]}" ]; then
  echo "ERROR: SECRET_KEY and WEBHOOK_SECRET must differ so they can be rotated independently." >&2
  fail=1
fi
if [ "${VALUE[MINIO_ROOT_USER]}" = "minioadmin" ]; then
  echo "ERROR: MINIO_ROOT_USER must not be minioadmin (the well-known default)." >&2
  fail=1
fi
# These get embedded in connection URLs by the compose file, where reserved
# characters cannot be escaped — one charset rule avoids broken DSNs.
for key in POSTGRES_PASSWORD REDIS_PASSWORD; do
  if [ "${SOURCE[$key]}" != "generated" ] \
     && ! printf '%s' "${VALUE[$key]}" | grep -qE '^[A-Za-z0-9._~-]+$'; then
    echo "ERROR: $key may only contain letters, digits, and . _ ~ -" >&2
    echo "       (it is embedded in connection URLs). Try: openssl rand -hex 24" >&2
    fail=1
  fi
done
# The signing secrets and MinIO credentials are not embedded in connection URLs,
# so the full charset above is too strict — but they ARE written into .env and
# expanded by the shell/compose, where a literal '$', whitespace, or a newline
# would corrupt the value (or splice extra lines into .env). Reject those.
for key in SECRET_KEY WEBHOOK_SECRET MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
  if [ "${SOURCE[$key]}" != "generated" ] \
     && { [[ "${VALUE[$key]}" =~ [[:space:]] ]] || [[ "${VALUE[$key]}" == *'$'* ]]; }; then
    echo "ERROR: $key must not contain '\$', whitespace, or newlines." >&2
    fail=1
  fi
done
if [ "$fail" -ne 0 ]; then exit 1; fi
for key in POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_PASSWORD; do
  if [ "${SOURCE[$key]}" != "generated" ] && [ "${#VALUE[$key]}" -lt 16 ]; then
    echo "WARNING: $key is under 16 characters. It only travels inside the" >&2
    echo "         Docker network, but longer is still safer." >&2
  fi
done

cp "$TEMPLATE" "$ENV_FILE"

# Fill in values, leaving every comment in place so the file stays
# self-documenting.
set_var() {
  local key="$1" val="$2"
  # sed is fine for generated hex, but user-supplied values can contain any
  # character, so splice with awk using an environment variable instead.
  if grep -qE "^${key}=" "$ENV_FILE"; then
    K="$key" V="$val" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]}
      index($0, k"=")==1 {print k"="v; next} {print}' "$ENV_FILE" > "${ENV_FILE}.tmp" \
      && mv "${ENV_FILE}.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

for key in $SECRETS $TUNABLES; do
  # Tunables left at their template default are already correct in the copy.
  [ "${SOURCE[$key]}" = "template" ] && continue
  set_var "$key" "${VALUE[$key]}"
done

chmod 600 "$ENV_FILE"

echo
echo "TraceIQ setup complete. Wrote $ENV_FILE (mode 600):"
echo
for key in $SECRETS $TUNABLES; do
  case "${SOURCE[$key]}" in
    user)      printf '  %-30s %s\n' "$key" "supplied by you (environment)" ;;
    prompt)    printf '  %-30s %s\n' "$key" "supplied by you (prompt)" ;;
    generated) printf '  %-30s %s\n' "$key" "auto-generated" ;;
    template)  printf '  %-30s %s\n' "$key" "template default" ;;
  esac
done

cat <<EOF

Every other setting (SMTP, AI provider, retention, ...) can be edited directly
in $ENV_FILE before you start.

Before starting, review these in $ENV_FILE:

  MINIO_PUBLIC_URL       must be reachable by your BROWSER, not just by Docker.
                         Change it if you serve TraceIQ to other machines.

  ALLOW_PRIVATE_NETWORK_TARGETS
                         false refuses tests against internal/private
                         addresses. Most self-hosters need true — read the
                         note in $ENV_FILE first, because true also lets any
                         user of this instance probe your network.

Then start it:

  docker compose -f docker-compose.community.yml --env-file $ENV_FILE up -d

Watch it come up (the backend applies migrations on first boot):

  docker compose -f docker-compose.community.yml logs -f backend

Then open http://localhost:8080 and register the first account.

Keep $ENV_FILE backed up somewhere safe. Losing SECRET_KEY logs everyone out;
losing POSTGRES_PASSWORD or the MinIO credentials loses access to your data.
EOF
