#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

SOURCE_DIR=$REPO_ROOT/bootstrap/codex-home
TARGET_DIR=${CODEX_HOME:-$HOME/.codex}

[ -d "$SOURCE_DIR" ] || fail "Source Codex home directory not found: $SOURCE_DIR"

install -d "$TARGET_DIR" "$TARGET_DIR/memories"

if [ -f "$SOURCE_DIR/AGENTS.md" ]; then
  install -m 644 "$SOURCE_DIR/AGENTS.md" "$TARGET_DIR/AGENTS.md"
fi

if [ -f "$SOURCE_DIR/memories/machine-notes.md" ] && [ ! -f "$TARGET_DIR/memories/machine-notes.md" ]; then
  install -m 644 "$SOURCE_DIR/memories/machine-notes.md" "$TARGET_DIR/memories/machine-notes.md"
fi

log "Synced repo-managed Codex home defaults to $TARGET_DIR"
