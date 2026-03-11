#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

# shellcheck disable=SC1091
. "$SCRIPT_DIR/../lib/common.sh"

restart_ssh_service() {
  if systemctl restart ssh 2>/dev/null; then
    return 0
  fi
  systemctl restart sshd
}

configure_ufw() {
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow in on tailscale0
  ufw deny 22/tcp
  ufw --force enable
}

[ $# -eq 1 ] || fail "Usage: bootstrap/remote/lockdown-ssh.sh <env-file>"

ENV_FILE=$1
[ "$(id -u)" -eq 0 ] || fail "This script must run as root"
load_env_file "$ENV_FILE"

log "Disabling direct root login"
write_if_changed "/etc/ssh/sshd_config.d/90-bootstrap-lockdown.conf" <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
restart_ssh_service

log "Applying firewall rules"
configure_ufw

log "SSH lock-down complete"
