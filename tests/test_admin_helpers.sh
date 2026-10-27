#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/bin"
printf 'test-key\n' > "$TMP_DIR/key"
printf 'ssh-ed25519 AAAAexample test\n' > "$TMP_DIR/key.pub"

# Replace only the transport. Each local helper runs with its normal inputs.
cat > "$TMP_DIR/bin/ssh" <<'SSH'
#!/usr/bin/env bash
set -euo pipefail
target=${@: -2:1}
command=${@: -1}
printf '%s %s\n' "$target" "$command" >> "$TEST_SSH_LOG"
case "$target" in
  *@server-example) [ "$target" = "$TEST_ADMIN_USER@server-example" ] ;;
  *@192.0.2.10) [ "$command" != true ] ;;
esac
cat >/dev/null
SSH
cat > "$TMP_DIR/bin/scp" <<'SCP'
#!/usr/bin/env bash
exit 0
SCP
chmod +x "$TMP_DIR/bin/ssh" "$TMP_DIR/bin/scp"

cat > "$TMP_DIR/host.env" <<'ENV'
HOST_PUBLIC_IPV4=192.0.2.10
HOSTNAME=server-example
TAILSCALE_HOSTNAME=server-example
TAILSCALE_AUTHKEY=test-auth-key
TUNNEL_TOKEN=test-tunnel-token
TUNNEL_HOSTNAMES=app.example.com
SMOKE_HOSTNAME=app.example.com
ENV

run_helper() {
  local script=$1
  shift
  : > "$TMP_DIR/ssh.log"
  env -u ADMIN_USER -u BOOTSTRAP_REMOTE_DIR \
    PATH="$TMP_DIR/bin:$PATH" \
    TEST_ADMIN_USER="$expected_user" TEST_SSH_LOG="$TMP_DIR/ssh.log" \
    BOOTSTRAP_SSH_KEY="$TMP_DIR/key" \
    BOOTSTRAP_SKIP_CLOUDFLARE=1 BOOTSTRAP_SKIP_SMOKE_CHECK=1 \
    "$REPO_ROOT/bootstrap/local/$script" "$TMP_DIR/host.env" "$@" </dev/null
  grep -Fq "$expected_user@server-example " "$TMP_DIR/ssh.log"
}

for expected_user in admin operator; do
  if [ "$expected_user" = operator ]; then
    printf 'ADMIN_USER=operator\n' >> "$TMP_DIR/host.env"
  fi
  run_helper bootstrap-host.sh
  run_helper reconcile-host.sh
  grep -Fq "tar -xzf - -C '/home/$expected_user/.cache/bootstrap-bundle'" "$TMP_DIR/ssh.log"
  run_helper codex-login.sh
  run_helper restart-proxy.sh
  run_helper sync-codex-skills.sh
  run_helper test-telegram-alert.sh 'test message'
  run_helper configure-deploy-target.sh example /srv/stacks/example https://app.example.com
  run_helper configure-config-sync.sh example https://example.com/repo.git deploy/vps /srv/stacks/example https://app.example.com
  run_helper prune-deploy-images.sh example 2
done
