#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/restart-proxy.sh <env-file>

Restart the managed Caddy proxy stack on an already-bootstrapped host over
Tailscale SSH so new route files under /srv/stacks/proxy/sites take effect.
EOF
}

[ $# -eq 1 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
ADMIN_USER=${ADMIN_USER:-makon}

require_command ssh
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  "docker compose -f /srv/stacks/proxy/compose.yaml restart caddy && docker compose -f /srv/stacks/proxy/compose.yaml ps"
