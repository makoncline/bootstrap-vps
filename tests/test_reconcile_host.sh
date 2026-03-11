#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/reconcile-host"
BIN_DIR="$TMP_DIR/bin"
LOG_FILE="$TMP_DIR/ssh.log"

rm -rf "$TMP_DIR"
mkdir -p "$BIN_DIR" "$TMP_DIR/captured"
printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForReconcile tests\n' >"$TMP_DIR/testkey.pub"

cat >"$BIN_DIR/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\n' "$*" >>"${TEST_SSH_LOG:?}"
case "$*" in
  *"tar -xzf - -C '/home/makon/.cache/bootstrap-bundle'"*) cat >/dev/null ;;
  *"cat > '/home/makon/.cache/bootstrap-bundle/host.env'"*)
    cat >"${TEST_CAPTURE_DIR:?}/host.env"
    ;;
esac
exit 0
EOF
chmod +x "$BIN_DIR/ssh"

cat >"$TMP_DIR/good.env" <<'EOF'
HOST_PUBLIC_IPV4=5.78.181.31
HOSTNAME=makon-dev-0
TAILSCALE_AUTHKEY=tskey-test
TAILSCALE_HOSTNAME=makon-dev-0
TUNNEL_TOKEN=tunnel-token
TUNNEL_ID=11111111-2222-3333-4444-555555555555
SMOKE_HOSTNAME=whoami.makon.dev
TUNNEL_HOSTNAMES=*.makon.dev,*.daylilycatalog.com
TELEGRAM_BOT_TOKEN=telegram-token
TELEGRAM_CHAT_ID=123456
HEALTHCHECK_DISK_PCT=90
EOF

PATH="$BIN_DIR:$PATH" \
TEST_SSH_LOG="$LOG_FILE" \
TEST_CAPTURE_DIR="$TMP_DIR/captured" \
BOOTSTRAP_SSH_KEY="$TMP_DIR/testkey" \
"$REPO_ROOT/bootstrap/local/reconcile-host.sh" "$TMP_DIR/good.env"

grep -q 'makon@makon-dev-0 .*init-host.sh' "$LOG_FILE"
grep -q 'TELEGRAM_BOT_TOKEN=telegram-token' "$TMP_DIR/captured/host.env"
grep -q 'TELEGRAM_CHAT_ID=123456' "$TMP_DIR/captured/host.env"
grep -q 'HEALTHCHECK_DISK_PCT=90' "$TMP_DIR/captured/host.env"

PATH="$BIN_DIR:$PATH" \
TEST_SSH_LOG="$LOG_FILE" \
ADMIN_USER=makon \
"$REPO_ROOT/bootstrap/local/test-telegram-alert.sh" "$TMP_DIR/good.env" "hello from test"

grep -q 'makon@makon-dev-0 notify' "$LOG_FILE"
