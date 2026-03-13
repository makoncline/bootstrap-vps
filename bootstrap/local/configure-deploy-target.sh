#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/configure-deploy-target.sh <env-file> <target> <stack-dir> <smoke-url>

Create or update a named deploy target on an already-bootstrapped host over Tailscale SSH.

Optional environment overrides:
  ADMIN_USER=makon
  DEPLOY_NAME="Daylily Catalog"
  DEPLOY_COMPOSE_FILE=/srv/stacks/daylilycatalog/compose.yaml
  DEPLOY_STACK_ENV_FILE=/srv/stacks/daylilycatalog/.env
  DEPLOY_CACHE_DIR=/srv/stacks/daylilycatalog/next-cache
  DEPLOY_CACHE_UID=1001
  DEPLOY_CACHE_GID=1001
  DEPLOY_AUTO_ROLLBACK=1
  DEPLOY_IMAGE_REPOSITORY=ghcr.io/makoncline/daylilycatalog
  DEPLOY_KEEP_IMAGE_COUNT=2
EOF
}

[ $# -eq 4 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
TARGET=$2
STACK_DIR=$3
SMOKE_URL=$4
ADMIN_USER=${ADMIN_USER:-makon}
DEPLOY_NAME=${DEPLOY_NAME:-$TARGET}
DEPLOY_COMPOSE_FILE=${DEPLOY_COMPOSE_FILE:-$STACK_DIR/compose.yaml}
DEPLOY_STACK_ENV_FILE=${DEPLOY_STACK_ENV_FILE:-$STACK_DIR/.env}
DEPLOY_CACHE_DIR=${DEPLOY_CACHE_DIR:-$STACK_DIR/next-cache}
DEPLOY_CACHE_UID=${DEPLOY_CACHE_UID:-1001}
DEPLOY_CACHE_GID=${DEPLOY_CACHE_GID:-1001}
DEPLOY_AUTO_ROLLBACK=${DEPLOY_AUTO_ROLLBACK:-1}
DEPLOY_IMAGE_REPOSITORY=${DEPLOY_IMAGE_REPOSITORY:-}
DEPLOY_KEEP_IMAGE_COUNT=${DEPLOY_KEEP_IMAGE_COUNT:-0}

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
DEPLOY_NAME=$(printf '%q' "$DEPLOY_NAME")
STACK_DIR=$(printf '%q' "$STACK_DIR")
COMPOSE_FILE=$(printf '%q' "$DEPLOY_COMPOSE_FILE")
STACK_ENV_FILE=$(printf '%q' "$DEPLOY_STACK_ENV_FILE")
SMOKE_URL=$(printf '%q' "$SMOKE_URL")
CACHE_DIR=$(printf '%q' "$DEPLOY_CACHE_DIR")
CACHE_UID=$(printf '%q' "$DEPLOY_CACHE_UID")
CACHE_GID=$(printf '%q' "$DEPLOY_CACHE_GID")
AUTO_ROLLBACK=$(printf '%q' "$DEPLOY_AUTO_ROLLBACK")
IMAGE_REPOSITORY=$(printf '%q' "$DEPLOY_IMAGE_REPOSITORY")
KEEP_IMAGE_COUNT=$(printf '%q' "$DEPLOY_KEEP_IMAGE_COUNT")
EOF

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  "sudo install -d -m 750 -o root -g '$ADMIN_USER' /etc/bootstrap/deploy-hooks && sudo tee '/etc/bootstrap/deploy-hooks/$TARGET.env' >/dev/null && sudo chown root:'$ADMIN_USER' '/etc/bootstrap/deploy-hooks/$TARGET.env' && sudo chmod 640 '/etc/bootstrap/deploy-hooks/$TARGET.env'" \
  <"$TARGET_FILE"

log "Configured deploy target '$TARGET' on $TAILSCALE_HOSTNAME"
