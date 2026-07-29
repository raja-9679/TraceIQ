#!/usr/bin/env bash
# TraceIQ one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/raja-9679/TraceIQ/main/install.sh | bash
#
# Downloads the community compose file, generates per-install secrets, pulls
# the prebuilt images, and starts the stack. It installs nothing on the host
# beyond a ./traceiq directory — remove the stack with:
#
#   cd traceiq && docker compose -f docker-compose.community.yml --env-file .env down
#
# Configuration (set as env vars before running):
#   TRACEIQ_DIR   install directory            (default: ./traceiq)
#   TRACEIQ_REF   git ref to fetch files from  (default: main)
#
# Bringing your own secrets/settings? Three ways, all before anything starts:
#
#   # 1. Inline env vars — anything you set wins over auto-generation
#   curl -fsSL .../install.sh | POSTGRES_PASSWORD='my-own-pass' bash
#
#   # 2. Answer prompts (works under curl|bash — reads your terminal directly)
#   curl -fsSL .../install.sh | bash -s -- --interactive
#
#   # 3. Write .env, stop, review/edit it, then start yourself
#   curl -fsSL .../install.sh | bash -s -- --configure
set -euo pipefail

# Everything lives inside main() so a truncated download can never execute a
# half-fetched script.
main() {
  local repo_raw dir ref configure_only=0 setup_args=()
  ref="${TRACEIQ_REF:-main}"
  dir="${TRACEIQ_DIR:-traceiq}"
  repo_raw="https://raw.githubusercontent.com/raja-9679/TraceIQ/${ref}/infrastructure"

  local arg
  for arg in "$@"; do
    case "${arg}" in
      --configure) configure_only=1 ;;
      --interactive) setup_args+=("--interactive") ;;
      *) printf 'Unknown argument: %s\n' "${arg}" >&2; exit 2 ;;
    esac
  done

  say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
  die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

  # --- Preflight -----------------------------------------------------------
  command -v curl >/dev/null 2>&1 || die "curl is required."
  command -v openssl >/dev/null 2>&1 || die "openssl is required (used to generate your instance's secrets)."
  command -v docker >/dev/null 2>&1 || die "Docker is required. Install it from https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is required (docker compose version failed)."
  docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon. Is it running, and is your user in the docker group?"

  # --- Fetch the three distribution files ----------------------------------
  say "Installing into ${dir}/"
  mkdir -p "${dir}"
  cd "${dir}"

  say "Downloading TraceIQ compose files (${ref})..."
  local f
  for f in docker-compose.community.yml env.community.example traceiq-setup.sh; do
    curl -fsSL "${repo_raw}/${f}" -o "${f}" || die "Failed to download ${f} from ${repo_raw}"
  done
  chmod +x traceiq-setup.sh

  # --- Secrets --------------------------------------------------------------
  # traceiq-setup.sh is idempotent: it refuses to overwrite an existing .env,
  # so re-running this installer upgrades images without rotating secrets.
  # Any secret/setting exported in this shell's environment wins over
  # auto-generation inside the setup script.
  if [ -f .env ]; then
    say "Existing .env found — keeping your secrets and settings."
    ./traceiq-setup.sh "${setup_args[@]+"${setup_args[@]}"}" >/dev/null
  else
    say "Writing .env (mode 600)..."
    # Not silenced: the provenance summary (and any prompts) should be seen.
    ./traceiq-setup.sh "${setup_args[@]+"${setup_args[@]}"}"
  fi

  if [ "${configure_only}" -eq 1 ]; then
    say "--configure: stopping here so you can review the configuration."
    cat <<EOF

  Edit ${dir}/.env to your liking (DB password, ports, SMTP, AI provider —
  every setting is in there with comments). When you're happy, start with:

    cd ${dir}
    docker compose -f docker-compose.community.yml --env-file .env pull
    docker compose -f docker-compose.community.yml --env-file .env up -d

EOF
    exit 0
  fi

  # --- Pull and start -------------------------------------------------------
  say "Pulling images (first time is a few GB — grab a coffee)..."
  docker compose -f docker-compose.community.yml --env-file .env pull

  say "Starting TraceIQ..."
  docker compose -f docker-compose.community.yml --env-file .env up -d

  # --- Wait for the backend to become ready --------------------------------
  say "Waiting for the backend to finish first-boot setup (schema migration)..."
  local backend_port tries=0
  backend_port="$(grep -E '^BACKEND_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
  backend_port="${backend_port:-8000}"
  until curl -fsS "http://localhost:${backend_port}/health/ready" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "${tries}" -ge 60 ]; then
      printf '\n'
      say "Backend not ready after 5 minutes. It may still be starting; check:"
      printf '      cd %s && docker compose -f docker-compose.community.yml logs -f backend\n' "${dir}"
      exit 1
    fi
    printf '.'
    sleep 5
  done
  printf '\n'

  local frontend_port
  frontend_port="$(grep -E '^FRONTEND_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
  frontend_port="${frontend_port:-8080}"

  say "TraceIQ is up: http://localhost:${frontend_port}"
  cat <<EOF

  Register now — THE FIRST ACCOUNT BECOMES THE WORKSPACE ADMIN, so do it
  before exposing this instance to your network.

  Your secrets live in ${dir}/.env (mode 600). Back that file up: losing
  SECRET_KEY logs everyone out; losing the Postgres/MinIO passwords loses
  access to your data.

  Manage the stack from the ${dir}/ directory:
    docker compose -f docker-compose.community.yml --env-file .env logs -f
    docker compose -f docker-compose.community.yml --env-file .env down

  Serving to other machines? Edit MINIO_PUBLIC_URL and FRONTEND_BASE_URL in
  .env first — see the comments in that file.
EOF
}

main "$@"
