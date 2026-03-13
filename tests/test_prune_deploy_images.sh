#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/prune-deploy-images"
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
"$REPO_ROOT/bootstrap/local/prune-deploy-images.sh" \
  "$TMP_DIR/good.env" \
  daylilycatalog \
  2

grep -q "makon@makon-dev-0" "$LOG_FILE"
grep -q "bootstrap-prune-images.sh 'daylilycatalog' '2'" "$LOG_FILE"
