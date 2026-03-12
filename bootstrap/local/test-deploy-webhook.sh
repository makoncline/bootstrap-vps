#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/test-deploy-webhook.sh <env-file> <target> <image-tag> [--clear-cache]

POST to the managed deploy webhook using the configured hostname and token.
EOF
}

[ $# -ge 3 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
TARGET=$2
IMAGE_TAG=$3
shift 3
CLEAR_CACHE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --clear-cache) CLEAR_CACHE=1 ;;
    *) fail "Unknown argument: $1" ;;
  esac
  shift
done

require_command curl
require_command jq
load_env_file "$ENV_FILE"
require_var DEPLOY_WEBHOOK_HOSTNAME
require_var DEPLOY_WEBHOOK_TOKEN

payload=$(jq -cn --arg image_tag "$IMAGE_TAG" --argjson clear_cache "$CLEAR_CACHE" '{image_tag: $image_tag, clear_cache: ($clear_cache == 1)}')

curl --fail --silent --show-error \
  -X POST "https://$DEPLOY_WEBHOOK_HOSTNAME/deploy/$TARGET" \
  -H "Authorization: Bearer $DEPLOY_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$payload"
