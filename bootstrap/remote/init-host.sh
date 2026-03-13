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
  apt-get install -y ca-certificates curl git jq python3 rsync sudo ufw unattended-upgrades zsh zsh-autosuggestions zsh-syntax-highlighting
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

ensure_cursor_remote_sandbox() {
  local package_name package_version package_url temp_dir package_file
  package_name=cursor-sandbox-apparmor
  package_version=0.4.0
  package_url="https://downloads.cursor.com/lab/enterprise/${package_name}_${package_version}_all.deb"

  if ! dpkg-query -W -f='${Status} ${Version}\n' "$package_name" 2>/dev/null | grep -Fq "install ok installed ${package_version}"; then
    temp_dir=$(mktemp -d)
    package_file="$temp_dir/${package_name}.deb"
    curl -fsSL "$package_url" -o "$package_file"
    export DEBIAN_FRONTEND=noninteractive
    dpkg -i "$package_file"
    rm -rf "$temp_dir"
  fi

  python3 - <<'PY'
from pathlib import Path

path = Path("/etc/apparmor.d/cursor-sandbox-remote")
needle = "  network netlink raw,\n"
anchor = "  capability setpcap,\n"

if path.exists():
    text = path.read_text()
    if needle not in text:
        if anchor not in text:
            raise SystemExit(f"Missing anchor in {path}")
        path.write_text(text.replace(anchor, anchor + "\n" + needle))
PY

  if [ -f /etc/apparmor.d/cursor-sandbox-remote ]; then
    apparmor_parser -r /etc/apparmor.d/cursor-sandbox-remote
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

  if [ -f "$source_dir/config.toml" ] && [ ! -f "$target_dir/config.toml" ]; then
    install -o "$ADMIN_USER" -g "$ADMIN_USER" -m 644 "$source_dir/config.toml" "$target_dir/config.toml"
  fi
}

ensure_swapfile() {
  local swapfile size_mb
  swapfile=/swapfile
  size_mb=1024

  if ! grep -qs "^$swapfile " /proc/swaps; then
    if [ ! -f "$swapfile" ]; then
      if command -v fallocate >/dev/null 2>&1; then
        fallocate -l "${size_mb}M" "$swapfile"
      else
        dd if=/dev/zero of="$swapfile" bs=1M count="$size_mb" status=none
      fi
      chmod 600 "$swapfile"
      mkswap "$swapfile" >/dev/null
    fi
    swapon "$swapfile"
  fi

  if ! grep -Fq "$swapfile none swap sw 0 0" /etc/fstab; then
    printf '%s\n' "$swapfile none swap sw 0 0" >>/etc/fstab
  fi

  write_if_changed "/etc/sysctl.d/60-bootstrap-swap.conf" <<'EOF'
vm.swappiness = 10
EOF
  sysctl --load /etc/sysctl.d/60-bootstrap-swap.conf >/dev/null
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

ensure_deploy_webhook_config() {
  local temp_dir temp_file
  install -d -m 750 -o root -g "$ADMIN_USER" /etc/bootstrap /etc/bootstrap/deploy-hooks /etc/bootstrap/config-sync

  if [ -n "${DEPLOY_WEBHOOK_HOSTNAME:-}" ] || [ -n "${DEPLOY_WEBHOOK_TOKEN:-}" ]; then
    [ -n "${DEPLOY_WEBHOOK_HOSTNAME:-}" ] || fail "DEPLOY_WEBHOOK_TOKEN requires DEPLOY_WEBHOOK_HOSTNAME"
    [ -n "${DEPLOY_WEBHOOK_TOKEN:-}" ] || fail "DEPLOY_WEBHOOK_HOSTNAME requires DEPLOY_WEBHOOK_TOKEN"

    temp_dir=$(mktemp -d)
    temp_file=$temp_dir/deploy-webhook.env
    cat >"$temp_file" <<EOF
DEPLOY_WEBHOOK_HOSTNAME=$DEPLOY_WEBHOOK_HOSTNAME
DEPLOY_WEBHOOK_TOKEN=$DEPLOY_WEBHOOK_TOKEN
DEPLOY_WEBHOOK_PORT=9001
EOF
    if [ ! -f /etc/bootstrap/deploy-webhook.env ] || ! cmp -s "$temp_file" /etc/bootstrap/deploy-webhook.env; then
      cat "$temp_file" >/etc/bootstrap/deploy-webhook.env
    fi
    chown "root:$ADMIN_USER" /etc/bootstrap/deploy-webhook.env
    chmod 640 /etc/bootstrap/deploy-webhook.env
    rm -rf "$temp_dir"
  else
    rm -f /etc/bootstrap/deploy-webhook.env
  fi
}

ensure_deploy_scripts() {
  write_if_changed "/usr/local/bin/bootstrap-prune-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

strip_optional_quotes() {
  local value
  value=$1
  value=${value#\"}
  value=${value%\"}
  printf '%s' "$value"
}

read_image_tag() {
  local env_file line
  env_file=$1
  line=$(grep '^IMAGE_TAG=' "$env_file" | tail -1 || true)
  [ -n "$line" ] || return 1
  strip_optional_quotes "${line#IMAGE_TAG=}"
}

[ $# -ge 1 ] || fail "Usage: bootstrap-prune-images.sh <target> [keep-count]"

target=$1
keep_count_override=${2:-}

case "$target" in
  *[!A-Za-z0-9._-]*|'') fail "Invalid target name: $target" ;;
esac

target_env="/etc/bootstrap/deploy-hooks/$target.env"
[ -f "$target_env" ] || fail "Deploy target not found: $target"

set -a
. "$target_env"
set +a

stack_dir=${STACK_DIR:-/srv/stacks/$target}
stack_env_file=${STACK_ENV_FILE:-$stack_dir/.env}
image_repository=${IMAGE_REPOSITORY:-}
keep_count=${keep_count_override:-${KEEP_IMAGE_COUNT:-0}}

[ -n "$image_repository" ] || fail "IMAGE_REPOSITORY is missing in $target_env"
[ -f "$stack_env_file" ] || fail "Stack env file not found: $stack_env_file"
case "$keep_count" in
  ''|*[!0-9]*) fail "keep-count must be a positive integer" ;;
esac
[ "$keep_count" -ge 1 ] || fail "keep-count must be at least 1"

current_tag=$(read_image_tag "$stack_env_file")
[ -n "$current_tag" ] || fail "IMAGE_TAG is missing in $stack_env_file"
current_ref="$image_repository:$current_tag"

mapfile -t image_refs < <(docker image ls "$image_repository" --format '{{.Repository}}:{{.Tag}}')
[ "${#image_refs[@]}" -gt 0 ] || {
  printf 'No local images found for %s\n' "$image_repository"
  exit 0
}

declare -A keep_refs=()
declare -A seen_refs=()
ordered_refs=()

for ref in "${image_refs[@]}"; do
  if [ -z "$ref" ] || [ "${ref%:<none>}" != "$ref" ]; then
    continue
  fi
  if [ -n "${seen_refs[$ref]+x}" ]; then
    continue
  fi
  seen_refs[$ref]=1
  ordered_refs+=("$ref")
done

if [ -n "${seen_refs[$current_ref]+x}" ]; then
  keep_refs["$current_ref"]=1
fi

for ref in "${ordered_refs[@]}"; do
  if [ "${#keep_refs[@]}" -ge "$keep_count" ]; then
    break
  fi
  keep_refs["$ref"]=1
done

removed=0
for ref in "${ordered_refs[@]}"; do
  if [ -n "${keep_refs[$ref]+x}" ]; then
    continue
  fi
  docker image rm "$ref" >/dev/null
  removed=$((removed + 1))
done

printf 'Kept %s image(s) for %s and removed %s old image(s)\n' "${#keep_refs[@]}" "$image_repository" "$removed"
EOF
  chmod 755 /usr/local/bin/bootstrap-prune-images.sh

  write_if_changed "/usr/local/bin/bootstrap-compose-deploy.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

notify_message() {
  local message
  message=$1
  /usr/local/bin/notify "$message" >/dev/null 2>&1 || true
}

schedule_proxy_restart() {
  nohup bash -lc 'sleep 2; docker compose -f /srv/stacks/proxy/compose.yaml restart caddy >/dev/null 2>&1' >/dev/null 2>&1 &
}

strip_optional_quotes() {
  local value
  value=$1
  value=${value#\"}
  value=${value%\"}
  printf '%s' "$value"
}

read_image_tag() {
  local env_file line
  env_file=$1
  line=$(grep '^IMAGE_TAG=' "$env_file" | tail -1 || true)
  [ -n "$line" ] || return 1
  strip_optional_quotes "${line#IMAGE_TAG=}"
}

update_image_tag() {
  local env_file image_tag temp_file
  env_file=$1
  image_tag=$2
  temp_file=$(mktemp)
  awk -v tag="$image_tag" '
    BEGIN { updated = 0 }
    /^IMAGE_TAG=/ {
      print "IMAGE_TAG=\"" tag "\""
      updated = 1
      next
    }
    { print }
    END {
      if (!updated) {
        print "IMAGE_TAG=\"" tag "\""
      }
    }
  ' "$env_file" >"$temp_file"
  cat "$temp_file" >"$env_file"
  rm -f "$temp_file"
}

ensure_cache_dir() {
  local cache_dir cache_uid cache_gid
  cache_dir=$1
  cache_uid=$2
  cache_gid=$3
  sudo install -d "$cache_dir"
  sudo chown "$cache_uid:$cache_gid" "$cache_dir"
}

clear_cache_dir() {
  local cache_dir
  cache_dir=$1
  [ -d "$cache_dir" ] || return 0
  sudo find "$cache_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

smoke_check() {
  local smoke_url attempts
  smoke_url=$1
  [ -n "$smoke_url" ] || return 0
  attempts=24
  while [ "$attempts" -gt 0 ]; do
    if curl --fail --silent --show-error --max-time 20 "$smoke_url" >/dev/null; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done
  return 1
}

run_compose_update() {
  local compose_file stack_dir stack_env_file
  compose_file=$1
  stack_dir=$2
  stack_env_file=$3
  (
    cd "$stack_dir"
    env -u IMAGE_TAG docker compose --env-file "$stack_env_file" -f "$compose_file" pull
    env -u IMAGE_TAG docker compose --env-file "$stack_env_file" -f "$compose_file" up -d
  )
}

[ $# -ge 2 ] || fail "Usage: bootstrap-compose-deploy.sh <target> <image-tag> [--clear-cache]"

target=$1
new_tag=$2
shift 2
clear_cache=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --clear-cache) clear_cache=1 ;;
    *) fail "Unknown argument: $1" ;;
  esac
  shift
done

case "$target" in
  *[!A-Za-z0-9._-]*|'') fail "Invalid target name: $target" ;;
esac

target_env="/etc/bootstrap/deploy-hooks/$target.env"
[ -f "$target_env" ] || fail "Deploy target not found: $target"

set -a
. "$target_env"
set +a

deploy_name=${DEPLOY_NAME:-$target}
stack_dir=${STACK_DIR:-/srv/stacks/$target}
compose_file=${COMPOSE_FILE:-$stack_dir/compose.yaml}
stack_env_file=${STACK_ENV_FILE:-$stack_dir/.env}
smoke_url=${SMOKE_URL:-}
cache_dir=${CACHE_DIR:-$stack_dir/next-cache}
cache_uid=${CACHE_UID:-1001}
cache_gid=${CACHE_GID:-1001}
auto_rollback=${AUTO_ROLLBACK:-1}
image_repository=${IMAGE_REPOSITORY:-}
keep_image_count=${KEEP_IMAGE_COUNT:-0}

[ -f "$compose_file" ] || fail "Compose file not found: $compose_file"
[ -f "$stack_env_file" ] || fail "Stack env file not found: $stack_env_file"

state_dir="$HOME/.local/state/bootstrap-deploy"
mkdir -p "$state_dir"
lock_file="$state_dir/$target.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
  printf 'deploy already in progress for %s\n' "$target" >&2
  exit 3
fi

previous_tag=$(read_image_tag "$stack_env_file")
[ -n "$previous_tag" ] || fail "IMAGE_TAG is missing in $stack_env_file"

ensure_cache_dir "$cache_dir" "$cache_uid" "$cache_gid"
if [ "$clear_cache" = "1" ]; then
  clear_cache_dir "$cache_dir"
fi

deploy_failed=0
rollback_status="not attempted"

update_image_tag "$stack_env_file" "$new_tag"
if ! run_compose_update "$compose_file" "$stack_dir" "$stack_env_file" || ! smoke_check "$smoke_url"; then
  deploy_failed=1
fi

if [ "$deploy_failed" = "0" ]; then
  case "$keep_image_count" in
    ''|*[!0-9]*) keep_image_count=0 ;;
  esac
  if [ -n "$image_repository" ] && [ "$keep_image_count" -ge 1 ]; then
    /usr/local/bin/bootstrap-prune-images.sh "$target" "$keep_image_count" >/dev/null 2>&1 || true
  fi
  notify_message "${deploy_name} deploy successful
previous image tag: ${previous_tag}
new image tag: ${new_tag}
url: ${smoke_url:-n/a}"
  exit 0
fi

if [ "$auto_rollback" = "1" ] && [ "$previous_tag" != "$new_tag" ]; then
  update_image_tag "$stack_env_file" "$previous_tag"
  if run_compose_update "$compose_file" "$stack_dir" "$stack_env_file" && smoke_check "$smoke_url"; then
    rollback_status="successful"
  else
    rollback_status="failed"
  fi
else
  rollback_status="disabled"
fi

notify_message "${deploy_name} deploy failed
previous image tag: ${previous_tag}
attempted image tag: ${new_tag}
rollback: ${rollback_status}
url: ${smoke_url:-n/a}"
exit 1
EOF
  chmod 755 /usr/local/bin/bootstrap-compose-deploy.sh

  write_if_changed "/usr/local/bin/bootstrap-sync-config.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

notify_message() {
  local message
  message=$1
  /usr/local/bin/notify "$message" >/dev/null 2>&1 || true
}

schedule_proxy_restart() {
  nohup bash -lc 'sleep 2; docker compose -f /srv/stacks/proxy/compose.yaml restart caddy >/dev/null 2>&1' >/dev/null 2>&1 &
}

copy_if_changed() {
  local source target
  source=$1
  target=$2
  if [ -f "$target" ] && cmp -s "$source" "$target"; then
    return 1
  fi
  install -d "$(dirname "$target")"
  cat "$source" >"$target"
  return 0
}

smoke_check() {
  local smoke_url attempts
  smoke_url=$1
  [ -n "$smoke_url" ] || return 0
  attempts=24
  while [ "$attempts" -gt 0 ]; do
    if curl --fail --silent --show-error --max-time 20 "$smoke_url" >/dev/null; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done
  return 1
}

run_compose_apply() {
  local stack_dir compose_dest stack_env_file
  stack_dir=$1
  compose_dest=$2
  stack_env_file=$3
  (
    cd "$stack_dir"
    env -u IMAGE_TAG docker compose --env-file "$stack_env_file" -f "$compose_dest" up -d
  )
}

fetch_repo_ref() {
  local checkout_dir repo_url config_subdir git_ref
  checkout_dir=$1
  repo_url=$2
  config_subdir=$3
  git_ref=$4

  if [ ! -d "$checkout_dir/.git" ]; then
    install -d "$(dirname "$checkout_dir")"
    git clone --filter=blob:none --no-checkout "$repo_url" "$checkout_dir" >/dev/null 2>&1
    git -C "$checkout_dir" sparse-checkout init --cone >/dev/null 2>&1
    git -C "$checkout_dir" sparse-checkout set "$config_subdir" >/dev/null 2>&1
  fi

  git -C "$checkout_dir" fetch --depth 1 origin "$git_ref" >/dev/null 2>&1
  git -C "$checkout_dir" checkout --detach FETCH_HEAD >/dev/null 2>&1
}

[ $# -eq 2 ] || fail "Usage: bootstrap-sync-config.sh <target> <git-ref>"

target=$1
git_ref=$2

case "$target" in
  *[!A-Za-z0-9._-]*|'') fail "Invalid target name: $target" ;;
esac
case "$git_ref" in
  *[!A-Za-z0-9._/-]*|'') fail "Invalid git ref: $git_ref" ;;
esac

target_env="/etc/bootstrap/config-sync/$target.env"
[ -f "$target_env" ] || fail "Config-sync target not found: $target"

set -a
. "$target_env"
set +a

sync_name=${SYNC_NAME:-$target}
repo_url=${REPO_URL:-}
config_subdir=${CONFIG_SUBDIR:-deploy/vps}
stack_dir=${STACK_DIR:-/srv/stacks/$target}
compose_dest=${COMPOSE_DEST:-$stack_dir/compose.yaml}
route_dest=${ROUTE_DEST:-/srv/stacks/proxy/sites/20-$target.caddy}
stack_env_file=${STACK_ENV_FILE:-$stack_dir/.env}
checkout_dir=${CHECKOUT_DIR:-/var/lib/bootstrap-config-sync/$target}
smoke_url=${SMOKE_URL:-}

[ -n "$repo_url" ] || fail "REPO_URL is missing in $target_env"
[ -f "$stack_env_file" ] || fail "Stack env file not found: $stack_env_file"

state_dir="$HOME/.local/state/bootstrap-config-sync"
mkdir -p "$state_dir"
lock_file="$state_dir/$target.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
  printf 'config sync already in progress for %s\n' "$target" >&2
  exit 3
fi

fetch_repo_ref "$checkout_dir" "$repo_url" "$config_subdir" "$git_ref"

source_dir="$checkout_dir/$config_subdir"
compose_src="$source_dir/compose.yaml"
route_src="$source_dir/caddy-route.caddy"

[ -f "$compose_src" ] || fail "Missing compose.yaml in $source_dir"
[ -f "$route_src" ] || fail "Missing caddy-route.caddy in $source_dir"

compose_changed=0
route_changed=0
if copy_if_changed "$compose_src" "$compose_dest"; then
  compose_changed=1
fi
if copy_if_changed "$route_src" "$route_dest"; then
  route_changed=1
fi

if [ "$compose_changed" = "1" ]; then
  run_compose_apply "$stack_dir" "$compose_dest" "$stack_env_file"
fi

if ! smoke_check "$smoke_url"; then
  notify_message "${sync_name} config sync failed
git ref: ${git_ref}
compose changed: ${compose_changed}
route changed: ${route_changed}
url: ${smoke_url:-n/a}"
  exit 1
fi

notify_message "${sync_name} config sync successful
git ref: ${git_ref}
compose changed: ${compose_changed}
route changed: ${route_changed}
url: ${smoke_url:-n/a}"

if [ "$route_changed" = "1" ]; then
  schedule_proxy_restart
fi
EOF
  chmod 755 /usr/local/bin/bootstrap-sync-config.sh

  write_if_changed "/usr/local/bin/bootstrap-deploy-webhook.py" <<'EOF'
#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_PATH = "/etc/bootstrap/deploy-webhook.env"
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_env(path):
    env = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key] = value.strip().strip('"')
    return env


class Handler(BaseHTTPRequestHandler):
    server_version = "BootstrapDeployWebhook/1.0"

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _authorized(self):
        token = self.server.deploy_token
        auth = self.headers.get("Authorization", "")
        header_token = self.headers.get("X-Deploy-Token", "")
        return auth == f"Bearer {token}" or header_token == token

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"ok": True})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authorized():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return

        if not self.path.startswith("/deploy/"):
            pass

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"ok": False, "error": "invalid content length"})
            return

        if length <= 0 or length > 16384:
            self._json(400, {"ok": False, "error": "invalid request body"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "invalid json"})
            return

        command = None
        response_payload = {}
        target = None

        if self.path.startswith("/deploy/"):
            target = self.path.removeprefix("/deploy/")
            if not TARGET_RE.fullmatch(target):
                self._json(400, {"ok": False, "error": "invalid target"})
                return

            image_tag = payload.get("image_tag")
            clear_cache = bool(payload.get("clear_cache", False))
            if not isinstance(image_tag, str) or not IMAGE_TAG_RE.fullmatch(image_tag):
                self._json(400, {"ok": False, "error": "invalid image_tag"})
                return

            command = ["/usr/local/bin/bootstrap-compose-deploy.sh", target, image_tag]
            if clear_cache:
                command.append("--clear-cache")
            response_payload = {"target": target, "image_tag": image_tag}
        elif self.path.startswith("/sync-config/"):
            target = self.path.removeprefix("/sync-config/")
            if not TARGET_RE.fullmatch(target):
                self._json(400, {"ok": False, "error": "invalid target"})
                return

            git_ref = payload.get("git_ref")
            if not isinstance(git_ref, str) or not git_ref:
                self._json(400, {"ok": False, "error": "invalid git_ref"})
                return

            command = ["/usr/local/bin/bootstrap-sync-config.sh", target, git_ref]
            response_payload = {"target": target, "git_ref": git_ref}
        else:
            self._json(404, {"ok": False, "error": "not found"})
            return

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode == 0:
            self._json(200, {"ok": True, **response_payload})
            return
        if completed.returncode == 3:
            self._json(409, {"ok": False, "error": completed.stderr.strip() or "action in progress"})
            return

        self._json(
            500,
            {
                "ok": False,
                "error": completed.stderr.strip() or "deploy failed",
                **response_payload,
            },
        )

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))


def main():
    config = load_env(CONFIG_PATH)
    port = int(config.get("DEPLOY_WEBHOOK_PORT", "9001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.deploy_token = config["DEPLOY_WEBHOOK_TOKEN"]
    server.serve_forever()


if __name__ == "__main__":
    main()
EOF
  chmod 755 /usr/local/bin/bootstrap-deploy-webhook.py
}

ensure_deploy_webhook_service() {
  write_if_changed "/etc/systemd/system/bootstrap-deploy-webhook.service" <<EOF
[Unit]
Description=Bootstrap deploy webhook
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=$ADMIN_USER
Group=$ADMIN_USER
ExecStart=/usr/local/bin/bootstrap-deploy-webhook.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload

  if [ -f /etc/bootstrap/deploy-webhook.env ]; then
    systemctl enable --now bootstrap-deploy-webhook.service
    systemctl restart bootstrap-deploy-webhook.service
  else
    systemctl disable --now bootstrap-deploy-webhook.service 2>/dev/null || true
  fi
}

ensure_internal_firewall_rules() {
  if ! command -v ufw >/dev/null 2>&1; then
    return 0
  fi

  if ! ufw status 2>/dev/null | grep -q '^Status: active'; then
    return 0
  fi

  ufw delete allow in on docker0 to any port 9001 proto tcp >/dev/null 2>&1 || true

  if [ -n "${DEPLOY_WEBHOOK_HOSTNAME:-}" ] && [ -n "${DEPLOY_WEBHOOK_TOKEN:-}" ]; then
    if ! ufw status | grep -Fq '172.16.0.0/12'; then
      ufw allow from 172.16.0.0/12 to any port 9001 proto tcp >/dev/null
    fi
  else
    ufw delete allow from 172.16.0.0/12 to any port 9001 proto tcp >/dev/null 2>&1 || true
  fi
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

if [ -n "${DEPLOY_WEBHOOK_HOSTNAME:-}" ] && [ -n "${DEPLOY_WEBHOOK_TOKEN:-}" ]; then
  if ! systemctl is-active --quiet bootstrap-deploy-webhook; then
    append_failure "deploy webhook service is not active"
  fi
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
  install -d -m 755 -o "$ADMIN_USER" -g "$ADMIN_USER" /var/lib/bootstrap-config-sync
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
    extra_hosts:
      - "host.docker.internal:host-gateway"
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

  if [ -n "${DEPLOY_WEBHOOK_HOSTNAME:-}" ]; then
    write_if_changed "/srv/stacks/proxy/sites/05-deploy-webhook.caddy" <<EOF
@deploy_webhook host $DEPLOY_WEBHOOK_HOSTNAME
handle @deploy_webhook {
  reverse_proxy host.docker.internal:9001
}
EOF
  else
    rm -f /srv/stacks/proxy/sites/05-deploy-webhook.caddy
  fi
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
log "Configuring Cursor remote sandbox support"
ensure_cursor_remote_sandbox
log "Installing Codex home defaults"
ensure_codex_home_files
log "Installing Codex skills"
ensure_codex_skills
log "Configuring swap"
ensure_swapfile
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
ensure_deploy_webhook_config
ensure_deploy_scripts
ensure_deploy_webhook_service
ensure_internal_firewall_rules
ensure_healthcheck_scripts
ensure_healthcheck_timer
log "Initial host bootstrap complete"
