#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TARGET_FILE="$REPO_ROOT/bootstrap/remote/init-host.sh"

SYNC_BLOCK=$(awk '
  /write_if_changed "\/usr\/local\/bin\/bootstrap-sync-config\.sh" <<'\''EOF'\''/ {in_block=1; next}
  in_block && /^EOF$/ {in_block=0; exit}
  in_block {print}
' "$TARGET_FILE")

printf '%s\n' "$SYNC_BLOCK" | grep -q "schedule_proxy_restart()"
printf '%s\n' "$SYNC_BLOCK" | grep -q "nohup bash -lc 'sleep 2; docker compose -f /srv/stacks/proxy/compose.yaml restart caddy >/dev/null 2>&1'"
printf '%s\n' "$SYNC_BLOCK" | grep -q "if \\[ \"\\\$route_changed\" = \"1\" \\]; then"
printf '%s\n' "$SYNC_BLOCK" | grep -q "schedule_proxy_restart"
grep -q "except BrokenPipeError:" "$TARGET_FILE"
