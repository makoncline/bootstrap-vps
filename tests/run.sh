#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

"$REPO_ROOT/tests/test_bootstrap_host.sh"
"$REPO_ROOT/tests/test_cloudflare_tunnel.sh"
"$REPO_ROOT/tests/test_reconcile_host.sh"
"$REPO_ROOT/tests/test_sync_local_codex_home.sh"
