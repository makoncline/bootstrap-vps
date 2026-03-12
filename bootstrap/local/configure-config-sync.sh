#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/configure-config-sync.sh <env-file> <target> <repo-url> <config-subdir> <stack-dir> <smoke-url>

Create or update a named config-sync target on an already-bootstrapped host over Tailscale SSH.

Optional environment overrides:
  ADMIN_USER=makon
  SYNC_NAME="Daylily Catalog"
  SYNC_COMPOSE_DEST=/srv/stacks/daylilycatalog/compose.yaml
  SYNC_ROUTE_DEST=/srv/stacks/proxy/sites/20-daylilycatalog.caddy
  SYNC_STACK_ENV_FILE=/srv/stacks/daylilycatalog/.env
  SYNC_CHECKOUT_DIR=/var/lib/bootstrap-config-sync/daylilycatalog
EOF
}

[ $# -eq 6 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
TARGET=$2
REPO_URL=$3
CONFIG_SUBDIR=$4
STACK_DIR=$5
SMOKE_URL=$6
ADMIN_USER=${ADMIN_USER:-makon}
SYNC_NAME=${SYNC_NAME:-$TARGET}
SYNC_COMPOSE_DEST=${SYNC_COMPOSE_DEST:-$STACK_DIR/compose.yaml}
SYNC_ROUTE_DEST=${SYNC_ROUTE_DEST:-/srv/stacks/proxy/sites/20-$TARGET.caddy}
SYNC_STACK_ENV_FILE=${SYNC_STACK_ENV_FILE:-$STACK_DIR/.env}
SYNC_CHECKOUT_DIR=${SYNC_CHECKOUT_DIR:-/var/lib/bootstrap-config-sync/$TARGET}

require_command ssh
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

case "$TARGET" in
  *[!A-Za-z0-9._-]*|'')
    fail "Target names may only contain letters, numbers, dots, underscores, and dashes"
    ;;
esac

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
TARGET_FILE=$WORK_DIR/"$TARGET.env"

cat >"$TARGET_FILE" <<EOF
SYNC_NAME=$(printf '%q' "$SYNC_NAME")
REPO_URL=$(printf '%q' "$REPO_URL")
CONFIG_SUBDIR=$(printf '%q' "$CONFIG_SUBDIR")
STACK_DIR=$(printf '%q' "$STACK_DIR")
COMPOSE_DEST=$(printf '%q' "$SYNC_COMPOSE_DEST")
ROUTE_DEST=$(printf '%q' "$SYNC_ROUTE_DEST")
STACK_ENV_FILE=$(printf '%q' "$SYNC_STACK_ENV_FILE")
CHECKOUT_DIR=$(printf '%q' "$SYNC_CHECKOUT_DIR")
SMOKE_URL=$(printf '%q' "$SMOKE_URL")
EOF

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  "sudo install -d -m 750 -o root -g '$ADMIN_USER' /etc/bootstrap/config-sync && sudo tee '/etc/bootstrap/config-sync/$TARGET.env' >/dev/null && sudo chown root:'$ADMIN_USER' '/etc/bootstrap/config-sync/$TARGET.env' && sudo chmod 640 '/etc/bootstrap/config-sync/$TARGET.env'" \
  <"$TARGET_FILE"

log "Configured config-sync target '$TARGET' on $TAILSCALE_HOSTNAME"
