#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/test-config-sync-webhook.sh <env-file> <target> <git-ref>

POST to the managed config-sync webhook using the configured hostname and token.
EOF
}

[ $# -eq 3 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
TARGET=$2
GIT_REF=$3

require_command curl
require_command jq
load_env_file "$ENV_FILE"
require_var DEPLOY_WEBHOOK_HOSTNAME
require_var DEPLOY_WEBHOOK_TOKEN

payload=$(jq -cn --arg git_ref "$GIT_REF" '{git_ref: $git_ref}')

curl --fail --silent --show-error \
  -X POST "https://$DEPLOY_WEBHOOK_HOSTNAME/sync-config/$TARGET" \
  -H "Authorization: Bearer $DEPLOY_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$payload"
