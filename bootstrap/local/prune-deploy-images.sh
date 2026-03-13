#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/prune-deploy-images.sh <env-file> <target> [keep-count]

Run the managed server-side image retention helper over Tailscale SSH.
If keep-count is omitted, the target's configured KEEP_IMAGE_COUNT is used.
EOF
}

[ $# -ge 2 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
TARGET=$2
KEEP_COUNT=${3:-}
ADMIN_USER=${ADMIN_USER:-makon}

require_command ssh
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

case "$TARGET" in
  *[!A-Za-z0-9._-]*|'')
    fail "Target names may only contain letters, numbers, dots, underscores, and dashes"
    ;;
esac

if [ -n "$KEEP_COUNT" ]; then
  case "$KEEP_COUNT" in
    *[!0-9]*|'') fail "keep-count must be a positive integer" ;;
  esac
fi

if [ -n "$KEEP_COUNT" ]; then
  remote_cmd="sudo /usr/local/bin/bootstrap-prune-images.sh '$TARGET' '$KEEP_COUNT'"
else
  remote_cmd="sudo /usr/local/bin/bootstrap-prune-images.sh '$TARGET'"
fi

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  "$remote_cmd"
