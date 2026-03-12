#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/restart-proxy"
BIN_DIR="$TMP_DIR/bin"
LOG_FILE="$TMP_DIR/ssh.log"

rm -rf "$TMP_DIR"
mkdir -p "$BIN_DIR"

cat >"$BIN_DIR/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\n' "$*" >>"${TEST_SSH_LOG:?}"
exit 0
EOF
chmod +x "$BIN_DIR/ssh"

cat >"$TMP_DIR/good.env" <<'EOF'
TAILSCALE_HOSTNAME=makon-dev-0
EOF

PATH="$BIN_DIR:$PATH" \
TEST_SSH_LOG="$LOG_FILE" \
ADMIN_USER=makon \
"$REPO_ROOT/bootstrap/local/restart-proxy.sh" "$TMP_DIR/good.env"

grep -q 'makon@makon-dev-0 docker compose -f /srv/stacks/proxy/compose.yaml restart caddy' "$LOG_FILE"
