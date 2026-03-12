#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/configure-config-sync"
BIN_DIR="$TMP_DIR/bin"
LOG_FILE="$TMP_DIR/ssh.log"
CAPTURE_DIR="$TMP_DIR/captured"

rm -rf "$TMP_DIR"
mkdir -p "$BIN_DIR" "$CAPTURE_DIR"

cat >"$BIN_DIR/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'ssh %s\n' "$*" >>"${TEST_SSH_LOG:?}"
cat >"${TEST_CAPTURE_DIR:?}/target.env"
exit 0
EOF
chmod +x "$BIN_DIR/ssh"

cat >"$TMP_DIR/good.env" <<'EOF'
TAILSCALE_HOSTNAME=makon-dev-0
EOF

PATH="$BIN_DIR:$PATH" \
TEST_SSH_LOG="$LOG_FILE" \
TEST_CAPTURE_DIR="$CAPTURE_DIR" \
ADMIN_USER=makon \
SYNC_NAME="Daylily Catalog" \
"$REPO_ROOT/bootstrap/local/configure-config-sync.sh" \
  "$TMP_DIR/good.env" \
  daylilycatalog \
  https://github.com/makoncline/new-daylily-catalog.git \
  deploy/vps \
  /srv/stacks/daylilycatalog \
  https://vps-test.daylilycatalog.com

grep -q "makon@makon-dev-0" "$LOG_FILE"
grep -q 'SYNC_NAME=Daylily\\ Catalog' "$CAPTURE_DIR/target.env"
grep -q 'REPO_URL=https://github.com/makoncline/new-daylily-catalog.git' "$CAPTURE_DIR/target.env"
grep -q 'CONFIG_SUBDIR=deploy/vps' "$CAPTURE_DIR/target.env"
grep -q 'CHECKOUT_DIR=/var/lib/bootstrap-config-sync/daylilycatalog' "$CAPTURE_DIR/target.env"
