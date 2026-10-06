#!/bin/sh
# Always include the override, including for upgrades/scaling/recreation.
set -eu
powerall_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "$powerall_root"
exec docker compose --env-file deploy/hosting.env -f compose.yaml -f compose.npm.yaml "$@"
