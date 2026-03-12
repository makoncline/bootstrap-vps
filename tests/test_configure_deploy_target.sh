#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/configure-deploy-target"
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
DEPLOY_NAME="Daylily Catalog" \
"$REPO_ROOT/bootstrap/local/configure-deploy-target.sh" \
  "$TMP_DIR/good.env" \
  daylilycatalog \
  /srv/stacks/daylilycatalog \
  https://vps-test.daylilycatalog.com

grep -q "makon@makon-dev-0" "$LOG_FILE"
grep -q 'DEPLOY_NAME=Daylily\\ Catalog' "$CAPTURE_DIR/target.env"
grep -q "STACK_DIR=/srv/stacks/daylilycatalog" "$CAPTURE_DIR/target.env"
grep -q "SMOKE_URL=https://vps-test.daylilycatalog.com" "$CAPTURE_DIR/target.env"
