#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/sync-local-codex-home"
TARGET_DIR="$TMP_DIR/codex-home"

rm -rf "$TMP_DIR"
mkdir -p "$TARGET_DIR"

CODEX_HOME="$TARGET_DIR" "$REPO_ROOT/bootstrap/local/sync-local-codex-home.sh"

test -f "$TARGET_DIR/AGENTS.md"
test -f "$TARGET_DIR/memories/machine-notes.md"
grep -q 'machine-notes.md' "$TARGET_DIR/AGENTS.md"
grep -q 'Short label' "$TARGET_DIR/memories/machine-notes.md"
