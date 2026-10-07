#!/bin/sh
# Keep the LAN/IP gateway override active for every deployment command.
set -eu
powerall_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "$powerall_root"
exec docker compose --env-file deploy/hosting.env -f compose.yaml -f compose.npm-ip.yaml "$@"
