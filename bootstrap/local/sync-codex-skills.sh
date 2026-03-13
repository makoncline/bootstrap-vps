#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

[ $# -eq 1 ] || fail "Usage: bootstrap/local/sync-codex-skills.sh <env-file>"

ENV_FILE=$1
ADMIN_USER=${ADMIN_USER:-makon}

require_command ssh
require_command tar
load_env_file "$ENV_FILE"
require_var TAILSCALE_HOSTNAME

tar_skills_to_stdout() {
  if [ "$(uname -s)" = "Darwin" ]; then
    COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 tar --no-mac-metadata --disable-copyfile --no-xattrs -C "$REPO_ROOT/bootstrap" -czf - codex-skills
  else
    tar -C "$REPO_ROOT/bootstrap" -czf - codex-skills
  fi
}

log "Syncing Codex skills to $ADMIN_USER@$TAILSCALE_HOSTNAME"
tar_skills_to_stdout | \
  ssh \
    -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$ADMIN_USER@$TAILSCALE_HOSTNAME" \
    "mkdir -p ~/.codex/skills && tar -xzf - -C ~/.codex/skills --strip-components=1"
