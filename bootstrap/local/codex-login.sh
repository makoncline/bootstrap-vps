#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

[ $# -eq 1 ] || fail "Usage: bootstrap/local/codex-login.sh <env-file>"

ENV_FILE=$1
ADMIN_USER=${ADMIN_USER:-makon}

require_command ssh
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

log "Launching Codex login on $ADMIN_USER@$TAILSCALE_HOSTNAME"
exec ssh \
  -t \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  'command -v codex >/dev/null 2>&1 || { echo "codex is not installed on this server"; exit 1; }; codex login --device-auth'
