#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TARGET="$REPO_ROOT/bootstrap/remote/init-host.sh"

grep -Fq 'ensure_codex_home_files' "$TARGET"
grep -Fq 'source_dir=$SCRIPT_DIR/../codex-home' "$TARGET"
grep -Fq 'config.toml' "$TARGET"
grep -Fq 'codex-home' "$REPO_ROOT/bootstrap/local/bootstrap-host.sh"
