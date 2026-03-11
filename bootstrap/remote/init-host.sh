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

REMOTE_INIT_REBOOT_REQUIRED_EXIT=42

ensure_base_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl git jq rsync sudo ufw unattended-upgrades zsh zsh-autosuggestions zsh-syntax-highlighting
}

ensure_system_upgrades() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get upgrade -y
}

needs_reboot() {
  [ -f /var/run/reboot-required ]
}

ensure_hostname() {
  current_hostname=$(hostnamectl --static 2>/dev/null || hostname)
  if [ "$current_hostname" != "$HOSTNAME" ]; then
    hostnamectl set-hostname "$HOSTNAME"
  fi
}

ensure_admin_user() {
  if ! id "$ADMIN_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups sudo "$ADMIN_USER"
  fi
  install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
  write_if_changed "/home/$ADMIN_USER/.ssh/authorized_keys" <<EOF
$ADMIN_SSH_PUBKEY
EOF
  chown "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh/authorized_keys"
  chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"
  usermod -aG docker "$ADMIN_USER" 2>/dev/null || true
  write_if_changed "/etc/sudoers.d/90-$ADMIN_USER-nopasswd" <<EOF
$ADMIN_USER ALL=(ALL) NOPASSWD:ALL
EOF
  chmod 440 "/etc/sudoers.d/90-$ADMIN_USER-nopasswd"
}

ensure_admin_shell() {
  local current_shell zsh_path rc_file rc_dir
  zsh_path=$(command -v zsh)
  current_shell=$(getent passwd "$ADMIN_USER" | cut -d: -f7)
  if [ "$current_shell" != "$zsh_path" ]; then
    usermod --shell "$zsh_path" "$ADMIN_USER"
  fi

  rc_dir="/home/$ADMIN_USER/.zshrc.d"
  rc_file="/home/$ADMIN_USER/.zshrc"
  install -d -m 755 -o "$ADMIN_USER" -g "$ADMIN_USER" "$rc_dir"

  write_if_changed "$rc_dir/bootstrap.zsh" <<'EOF'
export HISTFILE="${HISTFILE:-$HOME/.zsh_history}"
HISTSIZE=50000
SAVEHIST=50000

setopt AUTO_CD
setopt EXTENDED_GLOB
setopt HIST_IGNORE_DUPS
setopt HIST_IGNORE_SPACE
setopt INC_APPEND_HISTORY
setopt SHARE_HISTORY

mkdir -p "$HOME/.cache/zsh"
autoload -Uz compinit
compinit -d "$HOME/.cache/zsh/zcompdump-$ZSH_VERSION"

zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'

[ -f /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh ] && source /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh
[ -f /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ] && source /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

alias ll='ls -lah'
alias la='ls -A'
alias gs='git status -sb'

PROMPT='%F{cyan}%n@%m%f %F{yellow}%1~%f %# '

[ -f "$HOME/.zshrc.d/local.zsh" ] && source "$HOME/.zshrc.d/local.zsh"
EOF

  if [ ! -f "$rc_file" ]; then
    write_if_changed "$rc_file" <<'EOF'
[ -f "$HOME/.zshrc.d/bootstrap.zsh" ] && source "$HOME/.zshrc.d/bootstrap.zsh"
EOF
  elif ! grep -Fq '.zshrc.d/bootstrap.zsh' "$rc_file"; then
    cat >>"$rc_file" <<'EOF'

[ -f "$HOME/.zshrc.d/bootstrap.zsh" ] && source "$HOME/.zshrc.d/bootstrap.zsh"
EOF
  fi

  if [ ! -f "$rc_dir/local.zsh" ]; then
    write_if_changed "$rc_dir/local.zsh" <<'EOF'
# Add personal zsh overrides here.
EOF
  fi

  chown -R "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.zshrc" "$rc_dir"
}

ensure_ssh_base_config() {
  write_if_changed "/etc/ssh/sshd_config.d/60-bootstrap-base.conf" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
  restart_ssh_service
}

ensure_docker_repo() {
  install -d -m 0755 /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  write_if_changed "/etc/apt/sources.list.d/docker.list" <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && printf '%s' "$VERSION_CODENAME") stable
EOF
}

ensure_docker() {
  ensure_docker_repo
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  getent group docker >/dev/null 2>&1 || groupadd docker
  usermod -aG docker "$ADMIN_USER"
}

ensure_docker_daemon_config() {
  local temp_dir temp_file
  temp_dir=$(mktemp -d)
  temp_file=$temp_dir/daemon.json
  cat >"$temp_file" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
  if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_file" /etc/docker/daemon.json; then
    install -d /etc/docker
    cat "$temp_file" >/etc/docker/daemon.json
    systemctl restart docker
  fi
  rm -rf "$temp_dir"
}

ensure_tailscale_repo() {
  if [ ! -f /usr/share/keyrings/tailscale-archive-keyring.gpg ]; then
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  fi
  write_if_changed "/etc/apt/sources.list.d/tailscale.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main
EOF
}

ensure_tailscale() {
  ensure_tailscale_repo
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y tailscale
  systemctl enable --now tailscaled
  if tailscale status --json 2>/dev/null | jq -e '.BackendState == "Running"' >/dev/null; then
    tailscale up --ssh --hostname="$TAILSCALE_HOSTNAME" --accept-routes=false
  else
    tailscale up --ssh --auth-key="$TAILSCALE_AUTHKEY" --hostname="$TAILSCALE_HOSTNAME" --accept-routes=false
  fi
}

ensure_codex_cli() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y nodejs npm
  if ! command -v codex >/dev/null 2>&1; then
    npm install -g @openai/codex
  fi
}

ensure_codex_skills() {
  local source_dir target_dir
  source_dir=$SCRIPT_DIR/../codex-skills
  target_dir=/home/$ADMIN_USER/.codex/skills
  [ -d "$source_dir" ] || return 0
  install -d -m 755 -o "$ADMIN_USER" -g "$ADMIN_USER" "$target_dir"
  rsync -a "$source_dir"/ "$target_dir"/
  chown -R "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.codex"
}

ensure_codex_home_files() {
  local source_dir target_dir
  source_dir=$SCRIPT_DIR/../codex-home
  target_dir=/home/$ADMIN_USER/.codex
  [ -d "$source_dir" ] || return 0

  install -d -m 755 -o "$ADMIN_USER" -g "$ADMIN_USER" "$target_dir"
  install -d -m 755 -o "$ADMIN_USER" -g "$ADMIN_USER" "$target_dir/memories"

  if [ -f "$source_dir/AGENTS.md" ]; then
    install -o "$ADMIN_USER" -g "$ADMIN_USER" -m 644 "$source_dir/AGENTS.md" "$target_dir/AGENTS.md"
  fi

  if [ -f "$source_dir/memories/machine-notes.md" ] && [ ! -f "$target_dir/memories/machine-notes.md" ]; then
    install -o "$ADMIN_USER" -g "$ADMIN_USER" -m 644 "$source_dir/memories/machine-notes.md" "$target_dir/memories/machine-notes.md"
  fi
}

ensure_unattended_upgrades() {
  write_if_changed "/etc/apt/apt.conf.d/20auto-upgrades" <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

  write_if_changed "/etc/apt/apt.conf.d/52bootstrap-unattended-upgrades" <<'EOF'
Unattended-Upgrade::Origins-Pattern {
        "origin=Ubuntu,archive=${distro_codename}-security";
        "origin=Ubuntu,archive=${distro_codename}-updates";
};
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:30";
EOF
}

ensure_monitoring_config() {
  local temp_dir temp_file
  install -d -m 750 -o root -g "$ADMIN_USER" /etc/bootstrap
  install -m 600 "$ENV_FILE" /etc/bootstrap/server.env

  temp_dir=$(mktemp -d)
  temp_file=$temp_dir/telegram.env
  cat >"$temp_file" <<EOF
HOSTNAME=$HOSTNAME
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
EOF
  if [ ! -f /etc/bootstrap/telegram.env ] || ! cmp -s "$temp_file" /etc/bootstrap/telegram.env; then
    cat "$temp_file" >/etc/bootstrap/telegram.env
  fi
  chown "root:$ADMIN_USER" /etc/bootstrap/telegram.env
  chmod 640 /etc/bootstrap/telegram.env
  rm -rf "$temp_dir"
}

ensure_healthcheck_scripts() {
  write_if_changed "/usr/local/bin/bootstrap-alert.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 1 ]; then
  ENV_FILE=/etc/bootstrap/telegram.env
  message=$1
else
  ENV_FILE=${1:-/etc/bootstrap/telegram.env}
  message=${2:-}
fi

[ -f "$ENV_FILE" ] || exit 0

set -a
. "$ENV_FILE"
set +a

chat_id=${TELEGRAM_CHAT_ID#\#}
[ -n "$message" ] || exit 0
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || exit 0
[ -n "${chat_id:-}" ] || exit 0

curl --fail --silent --show-error \
  -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${chat_id}" \
  --data-urlencode "text=${message}" \
  >/dev/null
EOF
  chmod 755 /usr/local/bin/bootstrap-alert.sh

  write_if_changed "/usr/local/bin/notify" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

[ "$#" -ge 1 ] || {
  printf 'Usage: notify "message"\n' >&2
  exit 1
}

exec /usr/local/bin/bootstrap-alert.sh "$*"
EOF
  chmod 755 /usr/local/bin/notify

  write_if_changed "/usr/local/bin/bootstrap-healthcheck.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${1:-/etc/bootstrap/server.env}
STATE_DIR=/var/lib/bootstrap-health
STATE_FILE=$STATE_DIR/last-state
mkdir -p "$STATE_DIR"

[ -f "$ENV_FILE" ] || exit 0

set -a
. "$ENV_FILE"
set +a

disk_threshold=${HEALTHCHECK_DISK_PCT:-85}
failures=""

append_failure() {
  local item
  item=$1
  if [ -n "$failures" ]; then
    failures="${failures}; ${item}"
  else
    failures=$item
  fi
}

if ! systemctl is-active --quiet docker; then
  append_failure "docker service is not active"
fi

if ! systemctl is-active --quiet tailscaled; then
  append_failure "tailscaled service is not active"
fi

if ! tailscale status --json 2>/dev/null | jq -e '.BackendState == "Running"' >/dev/null; then
  append_failure "tailscale backend is not running"
fi

if ! docker compose -f /srv/stacks/proxy/compose.yaml ps --status running --services 2>/dev/null | grep -qx 'caddy'; then
  append_failure "proxy stack is not healthy"
fi

if ! docker compose -f /srv/stacks/tunnel/compose.yaml ps --status running --services 2>/dev/null | grep -qx 'cloudflared'; then
  append_failure "tunnel stack is not healthy"
fi

if ! curl --fail --silent --show-error --max-time 20 "https://${SMOKE_HOSTNAME}" >/dev/null; then
  append_failure "smoke check failed for https://${SMOKE_HOSTNAME}"
fi

disk_pct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$disk_pct" ] && [ "$disk_pct" -ge "$disk_threshold" ]; then
  append_failure "disk usage is ${disk_pct}% on /"
fi

previous_state="unknown"
if [ -f "$STATE_FILE" ]; then
  previous_state=$(cat "$STATE_FILE")
fi

if [ -n "$failures" ]; then
  current_state="fail:${failures}"
  if [ "$current_state" != "$previous_state" ]; then
    printf '%s\n' "$current_state" >"$STATE_FILE"
    /usr/local/bin/bootstrap-alert.sh "[${HOSTNAME}] healthcheck failed: ${failures}"
  fi
  exit 1
fi

printf '%s\n' "ok" >"$STATE_FILE"
if [ "$previous_state" != "ok" ]; then
  /usr/local/bin/bootstrap-alert.sh "[${HOSTNAME}] healthcheck recovered"
fi
EOF
  chmod 755 /usr/local/bin/bootstrap-healthcheck.sh
}

ensure_healthcheck_timer() {
  write_if_changed "/etc/systemd/system/bootstrap-healthcheck.service" <<'EOF'
[Unit]
Description=Bootstrap healthcheck
After=network-online.target docker.service tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/bootstrap-healthcheck.sh /etc/bootstrap/server.env
EOF

  write_if_changed "/etc/systemd/system/bootstrap-healthcheck.timer" <<'EOF'
[Unit]
Description=Run bootstrap healthcheck every 5 minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=5m
Unit=bootstrap-healthcheck.service

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now bootstrap-healthcheck.timer
}

ensure_stack_layout() {
  install -d -m 755 /srv/stacks/proxy/sites /srv/stacks/tunnel /srv/stacks/whoami
  chown -R "$ADMIN_USER:$ADMIN_USER" /srv/stacks
}

ensure_proxy_stack() {
  write_if_changed "/srv/stacks/proxy/compose.yaml" <<'EOF'
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    networks:
      edge:
        aliases:
          - caddy
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./sites:/etc/caddy/sites:ro
      - caddy_data:/data
      - caddy_config:/config

volumes:
  caddy_data:
  caddy_config:

networks:
  edge:
    external: true
EOF

  write_if_changed "/srv/stacks/proxy/Caddyfile" <<'EOF'
{
  auto_https off
  admin off
}

:80 {
  import /etc/caddy/sites/*.caddy
  respond "unconfigured host" 404
}
EOF

  write_if_changed "/srv/stacks/proxy/sites/10-whoami.caddy" <<EOF
@whoami host $SMOKE_HOSTNAME
handle @whoami {
  reverse_proxy whoami:80
}
EOF
}

ensure_tunnel_stack() {
  write_if_changed "/srv/stacks/tunnel/compose.yaml" <<'EOF'
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token ${TUNNEL_TOKEN}
    env_file:
      - .env
    networks:
      - edge

networks:
  edge:
    external: true
EOF

  write_if_changed "/srv/stacks/tunnel/.env" <<EOF
TUNNEL_TOKEN=$TUNNEL_TOKEN
EOF
  chmod 600 /srv/stacks/tunnel/.env
}

ensure_whoami_stack() {
  write_if_changed "/srv/stacks/whoami/compose.yaml" <<'EOF'
services:
  whoami:
    image: traefik/whoami:v1.10
    restart: unless-stopped
    networks:
      edge:
        aliases:
          - whoami

networks:
  edge:
    external: true
EOF
}

sync_stack_permissions() {
  chown -R "$ADMIN_USER:$ADMIN_USER" /srv/stacks
}

start_stacks() {
  docker network inspect edge >/dev/null 2>&1 || true
  docker network create edge >/dev/null 2>&1 || true
  docker network inspect edge >/dev/null 2>&1 || fail "Docker network edge was not available after create"
  docker compose -f /srv/stacks/whoami/compose.yaml up -d
  docker compose -f /srv/stacks/proxy/compose.yaml up -d
  docker compose -f /srv/stacks/tunnel/compose.yaml up -d
}

[ $# -eq 1 ] || fail "Usage: bootstrap/remote/init-host.sh <env-file>"

ENV_FILE=$1
[ "$(id -u)" -eq 0 ] || fail "This script must run as root"
load_env_file "$ENV_FILE"
require_var ADMIN_USER
require_var ADMIN_SSH_PUBKEY
require_var HOSTNAME
require_var SMOKE_HOSTNAME
require_var TAILSCALE_AUTHKEY
require_var TAILSCALE_HOSTNAME
require_var TUNNEL_TOKEN

grep -q 'Ubuntu' /etc/os-release || fail "This bootstrap currently targets Ubuntu"

log "Installing base packages"
ensure_base_packages
log "Applying available Ubuntu package upgrades"
ensure_system_upgrades
if needs_reboot && [ "${BOOTSTRAP_POST_REBOOT:-0}" != "1" ]; then
  log "Package upgrades require a reboot before bootstrap can continue"
  exit "$REMOTE_INIT_REBOOT_REQUIRED_EXIT"
fi
log "Setting hostname"
ensure_hostname
log "Creating admin user"
ensure_admin_user
log "Configuring admin shell"
ensure_admin_shell
log "Applying SSH base configuration"
ensure_ssh_base_config
log "Enabling unattended security updates"
ensure_unattended_upgrades
log "Installing Docker"
ensure_docker
log "Configuring Docker log rotation"
ensure_docker_daemon_config
log "Installing Tailscale"
ensure_tailscale
log "Installing Codex CLI"
ensure_codex_cli
log "Installing Codex home defaults"
ensure_codex_home_files
log "Installing Codex skills"
ensure_codex_skills
log "Writing proxy and sample app stacks"
ensure_stack_layout
ensure_proxy_stack
ensure_tunnel_stack
ensure_whoami_stack
sync_stack_permissions
log "Starting Docker stacks"
start_stacks
log "Installing monitoring scripts and timer"
ensure_monitoring_config
ensure_healthcheck_scripts
ensure_healthcheck_timer
log "Initial host bootstrap complete"
