#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/bootstrap-host"
BIN_DIR="$TMP_DIR/bin"
LOG_FILE="$TMP_DIR/ssh.log"

rm -rf "$TMP_DIR"
mkdir -p "$BIN_DIR" "$TMP_DIR/state" "$TMP_DIR/captured"
printf 'fake-private-key\n' >"$TMP_DIR/testkey"
printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForBootstrap tests\n' >"$TMP_DIR/testkey.pub"

cat >"$BIN_DIR/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log_file=${TEST_SSH_LOG:?}
state_dir=${TEST_STATE_DIR:?}
printf 'ssh %s\n' "$*" >>"$log_file"
case "$*" in
  *"tar -xzf - -C '/root/bootstrap-bundle'"*) cat >/dev/null ;;
esac
case "$*" in
  *"admin@server-example"*"tailscale debug prefs"*)
    [ "${TEST_FAIL_VERIFY:-0}" = 0 ] || exit 1
    ;;
  *"root@192.0.2.10"*"init-host.sh"*)
    count_file="$state_dir/init-count"
    count=0
    if [ -f "$count_file" ]; then
      count=$(cat "$count_file")
    fi
    count=$((count + 1))
    printf '%s' "$count" >"$count_file"
    if [ "${TEST_TRIGGER_REBOOT:-0}" = "1" ] && [ "$count" -eq 1 ]; then
      exit 42
    fi
    exit 0
    ;;
  *"root@192.0.2.10 nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &"*)
    : >"$state_dir/reboot-pending"
    exit 0
    ;;
  *"root@192.0.2.10 true"*)
    if [ -f "$state_dir/reboot-pending" ]; then
      rm -f "$state_dir/reboot-pending"
      : >"$state_dir/reboot-complete"
      exit 255
    fi
    if [ -f "$state_dir/lockdown-seen" ]; then
      exit 255
    fi
    exit 0
    ;;
  *"admin@192.0.2.10 true"*)
    if [ -f "$state_dir/lockdown-seen" ]; then
      exit 255
    fi
    exit 0
    ;;
  *"root@192.0.2.10"*"lockdown-ssh.sh"*)
    : >"$state_dir/lockdown-seen"
    exit 0
    ;;
esac
exit 0
EOF
chmod +x "$BIN_DIR/ssh"

cat >"$BIN_DIR/scp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'scp %s\n' "$*" >>"${TEST_SSH_LOG:?}"
eval "set -- $*"
src=${@: -2:1}
dest=${@: -1:1}
if [ -n "${TEST_SCP_CAPTURE_DIR:-}" ] && [ -f "$src" ]; then
  cp "$src" "${TEST_SCP_CAPTURE_DIR}/$(basename "$dest")"
fi
exit 0
EOF
chmod +x "$BIN_DIR/scp"

cat >"$TMP_DIR/good.env" <<'EOF'
HOST_PUBLIC_IPV4=192.0.2.10
HOSTNAME=server-example
TAILSCALE_AUTHKEY=tskey-test
TAILSCALE_HOSTNAME=server-example
TUNNEL_TOKEN=tunnel-token
TUNNEL_ID=11111111-2222-3333-4444-555555555555
SMOKE_HOSTNAME=whoami.example.com
TUNNEL_HOSTNAMES=*.example.com
TELEGRAM_BOT_TOKEN=telegram-token
TELEGRAM_CHAT_ID=123456
HEALTHCHECK_DISK_PCT=90
DEPLOY_WEBHOOK_HOSTNAME=deploy.example.com
DEPLOY_WEBHOOK_TOKEN=deploy-token
AI_PROXY_URL=http://100.64.0.2:8317/v1
AI_PROXY_MODEL=test-model
EOF

cat >"$TMP_DIR/bad.env" <<'EOF'
HOST_PUBLIC_IPV4=192.0.2.10
HOSTNAME=server-example
TAILSCALE_HOSTNAME=server-example
TUNNEL_TOKEN=tunnel-token
TUNNEL_ID=11111111-2222-3333-4444-555555555555
SMOKE_HOSTNAME=whoami.example.com
TUNNEL_HOSTNAMES=*.example.com
EOF

set +e
output=$(
  PATH="$BIN_DIR:$PATH" \
  TEST_SSH_LOG="$LOG_FILE" \
  BOOTSTRAP_SSH_KEY="$TMP_DIR/testkey" \
  ADMIN_USER=admin \
  BOOTSTRAP_SKIP_CLOUDFLARE=1 \
  BOOTSTRAP_SKIP_SMOKE_CHECK=1 \
  "$REPO_ROOT/bootstrap/local/bootstrap-host.sh" "$TMP_DIR/bad.env" 2>&1
)
status=$?
set -e
[ "$status" -ne 0 ] || {
  printf 'expected validation failure for missing TAILSCALE_AUTHKEY\n' >&2
  exit 1
}
printf '%s' "$output" | grep -q 'Required variable missing: TAILSCALE_AUTHKEY'

PATH="$BIN_DIR:$PATH" \
TEST_SSH_LOG="$LOG_FILE" \
TEST_STATE_DIR="$TMP_DIR/state" \
TEST_SCP_CAPTURE_DIR="$TMP_DIR/captured" \
TEST_TRIGGER_REBOOT=1 \
BOOTSTRAP_SSH_KEY="$TMP_DIR/testkey" \
ADMIN_USER=admin \
BOOTSTRAP_SKIP_CLOUDFLARE=1 \
BOOTSTRAP_SKIP_SMOKE_CHECK=1 \
"$REPO_ROOT/bootstrap/local/bootstrap-host.sh" "$TMP_DIR/good.env"

grep -c 'root@192.0.2.10 .*init-host.sh' "$LOG_FILE" | grep -q '^2$'
grep -q "root@192.0.2.10 nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &" "$LOG_FILE"
grep -q 'root@192.0.2.10 .*init-host.sh' "$LOG_FILE"
grep -q 'admin@server-example sudo -n tailscale debug prefs' "$LOG_FILE"
grep -q 'root@192.0.2.10 .*lockdown-ssh.sh' "$LOG_FILE"
grep -q 'TELEGRAM_BOT_TOKEN=telegram-token' "$TMP_DIR/captured/host.env"
grep -q 'TELEGRAM_CHAT_ID=123456' "$TMP_DIR/captured/host.env"
grep -q 'HEALTHCHECK_DISK_PCT=90' "$TMP_DIR/captured/host.env"
grep -q 'DEPLOY_WEBHOOK_HOSTNAME=deploy.example.com' "$TMP_DIR/captured/host.env"
grep -q 'DEPLOY_WEBHOOK_TOKEN=deploy-token' "$TMP_DIR/captured/host.env"
grep -q 'AI_PROXY_URL=http://100.64.0.2:8317/v1' "$TMP_DIR/captured/host.env"
grep -q 'AI_PROXY_MODEL=test-model' "$TMP_DIR/captured/host.env"

rm -f "$LOG_FILE"
if PATH="$BIN_DIR:$PATH" TEST_SSH_LOG="$LOG_FILE" TEST_STATE_DIR="$TMP_DIR/state" \
  TEST_FAIL_VERIFY=1 ADMIN_USER=admin BOOTSTRAP_SSH_KEY="$TMP_DIR/testkey" \
  BOOTSTRAP_SKIP_CLOUDFLARE=1 BOOTSTRAP_SKIP_SMOKE_CHECK=1 \
  "$REPO_ROOT/bootstrap/local/bootstrap-host.sh" "$TMP_DIR/good.env"; then
  printf 'expected access verification failure\n' >&2
  exit 1
fi
! grep -q 'lockdown-ssh.sh' "$LOG_FILE"

sed '/^AI_PROXY_MODEL=/d' "$TMP_DIR/good.env" >"$TMP_DIR/bad-proxy.env"
rm -f "$LOG_FILE"
if PATH="$BIN_DIR:$PATH" TEST_SSH_LOG="$LOG_FILE" ADMIN_USER=admin \
  AI_PROXY_URL=http://100.64.0.2:8317/v1 AI_PROXY_MODEL= \
  BOOTSTRAP_SSH_KEY="$TMP_DIR/testkey" \
  "$REPO_ROOT/bootstrap/local/bootstrap-host.sh" "$TMP_DIR/bad-proxy.env"; then
  printf 'expected proxy validation failure\n' >&2
  exit 1
fi
[ ! -f "$LOG_FILE" ]
