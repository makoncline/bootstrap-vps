#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/admin/.ssh"

# Run the production user setup with filesystem and account operations isolated.
sed -n '/^ensure_admin_user() {/,/^}/p' "$ROOT/bootstrap/remote/init-host.sh" | \
  sed "s|/home/|$TMP/home/|g; s|/etc/sudoers.d/|$TMP/|g" > "$TMP/function.sh"
. "$ROOT/bootstrap/lib/common.sh"
. "$TMP/function.sh"
id() { return 0; }
install() { mkdir -p "$TMP/home/admin/.ssh"; }
chown() { :; }
chmod() { :; }
usermod() { :; }
ADMIN_USER=admin
ADMIN_SSH_PUBKEY='ssh-ed25519 AAAAexample initial'
keys="$TMP/home/admin/.ssh/authorized_keys"
printf '%s\n' 'from="192.0.2.10",no-port-forwarding ssh-ed25519 AAAAexample restricted' 'ssh-ed25519 AAAAother other' > "$keys"
cp "$keys" "$TMP/expected"
ensure_admin_user
cmp "$keys" "$TMP/expected"
ADMIN_SSH_PUBKEY='ssh-ed25519 AAAAnew new'
ensure_admin_user
ensure_admin_user
printf '%s\n' "$ADMIN_SSH_PUBKEY" >> "$TMP/expected"
cmp "$keys" "$TMP/expected"

printf '%s' 'ssh-ed25519 AAAAother other' > "$keys"
cp "$keys" "$TMP/expected"
ensure_admin_user
printf '\n%s\n' "$ADMIN_SSH_PUBKEY" >> "$TMP/expected"
cmp "$keys" "$TMP/expected"
