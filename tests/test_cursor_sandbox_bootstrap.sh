#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TARGET="$REPO_ROOT/bootstrap/remote/init-host.sh"

grep -Fq 'cursor-sandbox-apparmor' "$TARGET"
grep -Fq 'network netlink raw,' "$TARGET"
grep -Fq 'apparmor_parser -r /etc/apparmor.d/cursor-sandbox-remote' "$TARGET"
grep -Fq 'ensure_cursor_remote_sandbox' "$TARGET"
