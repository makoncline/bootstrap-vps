#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

"$REPO_ROOT/tests/test_bootstrap_host.sh"
"$REPO_ROOT/tests/test_cloudflare_tunnel.sh"
"$REPO_ROOT/tests/test_configure_config_sync.sh"
"$REPO_ROOT/tests/test_configure_deploy_target.sh"
"$REPO_ROOT/tests/test_codex_home_bootstrap.sh"
"$REPO_ROOT/tests/test_cursor_sandbox_bootstrap.sh"
"$REPO_ROOT/tests/test_prune_deploy_images.sh"
"$REPO_ROOT/tests/test_reconcile_host.sh"
"$REPO_ROOT/tests/test_restart_proxy.sh"
"$REPO_ROOT/tests/test_swap_bootstrap.sh"
"$REPO_ROOT/tests/test_sync_config_restart_strategy.sh"
"$REPO_ROOT/tests/test_sync_local_codex_home.sh"
