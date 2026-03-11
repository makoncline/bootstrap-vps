#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

[ $# -ge 1 ] || fail "Usage: bootstrap/local/test-telegram-alert.sh <env-file> [message]"

ENV_FILE=$1
shift || true
MESSAGE=${1:-}
ADMIN_USER=${ADMIN_USER:-makon}

require_command ssh
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

if [ -z "$MESSAGE" ]; then
  MESSAGE="[$TAILSCALE_HOSTNAME] telegram test"
fi

printf -v ESCAPED_MESSAGE '%q' "$MESSAGE"
ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
  "notify $ESCAPED_MESSAGE"
