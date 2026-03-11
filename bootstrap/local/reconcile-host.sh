#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: bootstrap/local/reconcile-host.sh <env-file>

Push the current remote bootstrap bundle to an already-bootstrapped host over Tailscale SSH
and rerun the idempotent remote converger there.

Environment toggles:
  BOOTSTRAP_SSH_KEY=/path/to/private/key
EOF
}

REMOTE_INIT_REBOOT_REQUIRED_EXIT=42

ssh_cmd() {
  local target
  target=$1
  shift
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$target" "$@"
}

copy_bundle() {
  log "Copying remote bundle to $ADMIN_USER@$TAILSCALE_HOSTNAME"
  COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 tar -C "$REPO_ROOT/bootstrap" -czf - remote lib codex-skills codex-home | \
    ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"
  cat "$RUNTIME_ENV_FILE" | ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "cat > '$REMOTE_DIR/host.env'"
}

wait_for_tailscale_ssh_unavailable() {
  local attempts
  attempts=24
  log "Waiting for $TAILSCALE_HOSTNAME to stop accepting Tailscale SSH"
  while [ "$attempts" -gt 0 ]; do
    if ! ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "true" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done
  fail "Timed out waiting for Tailscale SSH to drop on $TAILSCALE_HOSTNAME"
}

wait_for_tailscale_ssh_available() {
  local attempts
  attempts=36
  log "Waiting for $TAILSCALE_HOSTNAME to accept Tailscale SSH again"
  while [ "$attempts" -gt 0 ]; do
    if ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "true" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done
  fail "Timed out waiting for Tailscale SSH to return on $TAILSCALE_HOSTNAME"
}

run_remote_init() {
  local status
  if ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "sudo '$REMOTE_DIR/remote/init-host.sh' '$REMOTE_DIR/host.env'"; then
    return 0
  else
    status=$?
  fi
  if [ "$status" -eq "$REMOTE_INIT_REBOOT_REQUIRED_EXIT" ]; then
    return "$REMOTE_INIT_REBOOT_REQUIRED_EXIT"
  fi
  fail "Remote reconcile failed with exit code $status"
}

request_remote_reboot() {
  log "Rebooting $HOSTNAME to finish package updates"
  ssh_cmd "$ADMIN_USER@$TAILSCALE_HOSTNAME" "sudo nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &"
}

append_env() {
  local key value
  key=$1
  value=${2-}
  printf '%s=%q\n' "$key" "$value" >>"$RUNTIME_ENV_FILE"
}

write_runtime_env_file() {
  : >"$RUNTIME_ENV_FILE"
  append_env HOST_PUBLIC_IPV4 "${HOST_PUBLIC_IPV4:-}"
  append_env HOST_PUBLIC_IPV6 "${HOST_PUBLIC_IPV6:-}"
  append_env HOSTNAME "$HOSTNAME"
  append_env TAILSCALE_AUTHKEY "$TAILSCALE_AUTHKEY"
  append_env TAILSCALE_HOSTNAME "$TAILSCALE_HOSTNAME"
  append_env TUNNEL_TOKEN "$TUNNEL_TOKEN"
  append_env CF_ZONE_ID "${CF_ZONE_ID:-}"
  append_env CF_API_TOKEN "${CF_API_TOKEN:-}"
  append_env CF_ACCOUNT_ID "${CF_ACCOUNT_ID:-}"
  append_env CF_ZONE_MAP "${CF_ZONE_MAP:-}"
  append_env TUNNEL_ID "${TUNNEL_ID:-}"
  append_env TUNNEL_HOSTNAMES "$TUNNEL_HOSTNAMES"
  append_env ADMIN_USER "$ADMIN_USER"
  append_env ADMIN_SSH_PUBKEY "$ADMIN_SSH_PUBKEY"
  append_env SMOKE_HOSTNAME "$SMOKE_HOSTNAME"
  append_env TELEGRAM_BOT_TOKEN "${TELEGRAM_BOT_TOKEN:-}"
  append_env TELEGRAM_CHAT_ID "${TELEGRAM_CHAT_ID:-}"
  append_env HEALTHCHECK_DISK_PCT "${HEALTHCHECK_DISK_PCT:-}"
  append_env BOOTSTRAP_POST_REBOOT "$BOOTSTRAP_POST_REBOOT"
}

[ $# -eq 1 ] || {
  usage >&2
  exit 1
}

ENV_FILE=$1
BOOTSTRAP_SSH_KEY=${BOOTSTRAP_SSH_KEY:-/Users/makon/.ssh/makon_admin_ed25519}
ADMIN_USER=${ADMIN_USER:-makon}
REMOTE_DIR=${BOOTSTRAP_REMOTE_DIR:-/home/$ADMIN_USER/.cache/bootstrap-bundle}

require_command ssh
require_command tar
load_env_file "$ENV_FILE"
require_var HOSTNAME
require_var TAILSCALE_AUTHKEY
require_var TAILSCALE_HOSTNAME
require_var TUNNEL_TOKEN
require_var TUNNEL_HOSTNAMES
require_var SMOKE_HOSTNAME
[ -f "${BOOTSTRAP_SSH_KEY}.pub" ] || fail "SSH public key not found: ${BOOTSTRAP_SSH_KEY}.pub"

ADMIN_SSH_PUBKEY=$(cat "${BOOTSTRAP_SSH_KEY}.pub")

WORK_DIR=$(mktemp -d)
RUNTIME_ENV_FILE=$WORK_DIR/host.env
trap 'rm -rf "$WORK_DIR"' EXIT

BOOTSTRAP_POST_REBOOT=0
write_runtime_env_file
copy_bundle

log "Running remote reconcile on $HOSTNAME"
set +e
run_remote_init
status=$?
set -e
if [ "$status" -eq "$REMOTE_INIT_REBOOT_REQUIRED_EXIT" ]; then
  request_remote_reboot
  wait_for_tailscale_ssh_unavailable
  wait_for_tailscale_ssh_available

  BOOTSTRAP_POST_REBOOT=1
  write_runtime_env_file
  copy_bundle

  log "Resuming remote reconcile on $HOSTNAME after reboot"
  set +e
  run_remote_init
  status=$?
  set -e
  if [ "$status" -eq "$REMOTE_INIT_REBOOT_REQUIRED_EXIT" ]; then
    fail "Remote reconcile still requires a reboot after the post-upgrade restart"
  fi
fi

log "Remote reconcile complete for $HOSTNAME"
