#!/usr/bin/env bash

log() {
  printf '[bootstrap] %s\n' "$*"
}

fail() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

trim() {
  local value
  value=$1
  value=${value#"${value%%[![:space:]]*}"}
  value=${value%"${value##*[![:space:]]}"}
  printf '%s' "$value"
}

first_csv_item() {
  local csv first
  csv=$1
  IFS=',' read -r first _rest <<EOF
$csv
EOF
  trim "$first"
}

split_csv_lines() {
  local csv old_ifs item
  csv=$1
  old_ifs=$IFS
  IFS=','
  for item in $csv; do
    trim "$item"
    printf '\n'
  done
  IFS=$old_ifs
}

load_env_file() {
  local env_file
  env_file=$1
  [ -f "$env_file" ] || fail "Env file not found: $env_file"
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
}

require_var() {
  local name value
  name=$1
  value=${!name-}
  [ -n "$value" ] || fail "Required variable missing: $name"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

write_if_changed() {
  local target temp_dir temp_file
  target=$1
  temp_dir=$(mktemp -d)
  temp_file=$temp_dir/content
  cat >"$temp_file"
  if [ ! -f "$target" ] || ! cmp -s "$temp_file" "$target"; then
    install -d "$(dirname "$target")"
    cat "$temp_file" >"$target"
  fi
  rm -rf "$temp_dir"
}
