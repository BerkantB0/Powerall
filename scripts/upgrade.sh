#!/bin/sh
# Apply the checked-out release to an existing NPM host-IP deployment.
set -eu
powerall_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "$powerall_root"
case "${1:-}" in
  --help|-h)
    printf '%s\n' 'Usage: ./scripts/upgrade.sh' \
      'Build and apply local code to an existing NPM host-IP deployment.' \
      'Preserves deployment secrets, volumes and existing replica counts.'
    exit 0 ;;
  '') ;;
  *) printf '%s\n' 'Usage: ./scripts/upgrade.sh' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { printf '%s\n' 'Unexpected arguments.' >&2; exit 2; }
compose="$powerall_root/scripts/compose-npm-ip.sh"
POWERALL_VERSION=$(cat VERSION)
export POWERALL_VERSION

"$compose" config --quiet
database_ids=$("$compose" ps --all -q database)
[ -n "$database_ids" ] || {
  printf '%s\n' 'No existing database container found. This script upgrades an installed deployment.' >&2
  exit 1
}
if ! mkdir .upgrade.lock 2>/dev/null; then
  printf '%s\n' 'Upgrade lock exists. Check for a running upgrade before removing .upgrade.lock.' >&2
  exit 1
fi
cleanup() {
  result=$?
  rmdir .upgrade.lock
  if [ "$result" -ne 0 ]; then
    printf '%s\n' 'Upgrade stopped. Check the error above; earlier steps may already have been applied.' >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

replicas() {
  ids=$("$compose" ps --all -q "$1")
  # Container IDs have no whitespace or shell metacharacters.
  set -- $ids
  if [ "$#" -gt 0 ]; then printf '%s\n' "$#"; else printf '1\n'; fi
}
api_count=$(replicas api)
worker_count=$(replicas worker)
frontend_count=$(replicas frontend)
gateway_count=$(replicas gateway)

printf 'Building Powerall %s from local source...\n' "$POWERALL_VERSION"
"$compose" build api frontend
"$compose" run --rm --no-deps --entrypoint caddy gateway validate --config /etc/caddy/Caddyfile --adapter caddyfile
# Never recreate PostgreSQL as part of an application upgrade.
"$compose" up -d --no-recreate --wait --wait-timeout 180 database
printf '%s\n' 'Applying compatible database migrations...'
"$compose" run --rm --no-deps migrate
printf '%s\n' 'Updating API, worker, frontend and gateway...'
"$compose" up -d --no-deps --force-recreate --wait --wait-timeout 180 --scale "api=$api_count" api
"$compose" up -d --no-deps --force-recreate --wait --wait-timeout 180 --scale "worker=$worker_count" worker
"$compose" up -d --no-deps --force-recreate --wait --wait-timeout 180 --scale "frontend=$frontend_count" frontend
"$compose" up -d --no-deps --force-recreate --wait --wait-timeout 180 --scale "gateway=$gateway_count" gateway
"$compose" ps
printf 'Powerall %s upgrade complete.\n' "$POWERALL_VERSION"
