#!/usr/bin/env bash
# Wrapper supervisord uses to launch app processes:
#
#   run-service.sh [--wait-for-backend] <env-file> <command...>
#
# Sources the env file rendered by entrypoint.sh (secrets live there, not in
# the supervisord config), optionally blocks until the backend is ready so
# consumers don't spam connection errors while migrations run, then execs.
set -euo pipefail

if [ "${1:-}" = "--wait-for-backend" ]; then
  shift
  until curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; do
    sleep 2
  done
fi

ENV_FILE="$1"
shift

# supervisord does not reset HOME when it drops privileges, so children see
# root's HOME=/root — and asyncpg then dies probing /root/.postgresql/ for
# client TLS keys it cannot read. Resolve HOME from the actual uid.
HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
export HOME

# Read the file literally, the way docker compose reads an env file. Shell
# `source` would perform quote removal — BACKEND_CORS_ORIGINS=["http://..."]
# would lose its inner quotes and stop being valid JSON for pydantic.
while IFS= read -r line; do
  case "${line}" in
    ''|'#'*) continue ;;
  esac
  export "${line?}"
done < "${ENV_FILE}"

exec "$@"
