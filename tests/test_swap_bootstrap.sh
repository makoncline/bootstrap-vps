#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TARGET="$REPO_ROOT/bootstrap/remote/init-host.sh"

grep -Fq 'ensure_swapfile' "$TARGET"
grep -Fq '/swapfile' "$TARGET"
grep -Fq 'vm.swappiness = 10' "$TARGET"
grep -Fq 'swapon "$swapfile"' "$TARGET"
