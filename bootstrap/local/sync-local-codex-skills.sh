#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

require_command rsync

SOURCE_DIR=$REPO_ROOT/bootstrap/codex-skills
TARGET_DIR=${CODEX_HOME:-$HOME/.codex}/skills

[ -d "$SOURCE_DIR" ] || fail "Source skills directory not found: $SOURCE_DIR"
install -d "$TARGET_DIR"

log "Syncing repo-managed Codex skills to $TARGET_DIR"
rsync -a "$SOURCE_DIR"/ "$TARGET_DIR"/
