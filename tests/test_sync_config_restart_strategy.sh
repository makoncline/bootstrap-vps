#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TARGET_FILE="$REPO_ROOT/bootstrap/remote/init-host.sh"

grep -q "schedule_proxy_restart()" "$TARGET_FILE"
grep -q "nohup bash -lc 'sleep 2; docker compose -f /srv/stacks/proxy/compose.yaml restart caddy >/dev/null 2>&1'" "$TARGET_FILE"
grep -q "except BrokenPipeError:" "$TARGET_FILE"
