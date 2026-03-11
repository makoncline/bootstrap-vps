#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$REPO_ROOT/bootstrap/lib/common.sh"

cf_api() {
  local method path body
  method=$1
  path=$2
  body=${3-}
  if [ -n "$body" ]; then
    curl --fail --silent --show-error \
      -X "$method" \
      -H "Authorization: Bearer $CF_API_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "$CF_API_BASE$path"
  else
    curl --fail --silent --show-error \
      -X "$method" \
      -H "Authorization: Bearer $CF_API_TOKEN" \
      -H "Content-Type: application/json" \
      "$CF_API_BASE$path"
  fi
}

zone_entries() {
  split_csv_lines "$CF_ZONE_MAP"
}

host_without_wildcard() {
  local host
  host=$1
  case "$host" in
    \*.*) printf '%s' "${host#*.}" ;;
    *) printf '%s' "$host" ;;
  esac
}

zone_id_for_hostname() {
  local hostname entry zone_id zone_name host best_name best_id
  hostname=$1
  host=$(host_without_wildcard "$hostname")
  best_name=
  best_id=
  for entry in $(zone_entries); do
    zone_name=${entry%%:*}
    zone_id=${entry#*:}
    [ -n "$zone_name" ] || continue
    [ "$zone_id" != "$entry" ] || fail "Invalid CF_ZONE_MAP entry: $entry"
    if [ "$host" = "$zone_name" ] || [[ "$host" == *."$zone_name" ]]; then
      if [ -z "$best_name" ] || [ "${#zone_name}" -gt "${#best_name}" ]; then
        best_name=$zone_name
        best_id=$zone_id
      fi
    fi
  done
  [ -n "$best_id" ] || fail "No matching zone found in CF_ZONE_MAP for hostname: $hostname"
  printf '%s' "$best_id"
}

hostname_matches_pattern() {
  local host pattern suffix
  host=$1
  pattern=$2
  case "$pattern" in
    \*.*)
      suffix=${pattern#*.}
      [[ "$host" == *."$suffix" ]] || return 1
      ;;
    *)
      [ "$host" = "$pattern" ] || return 1
      ;;
  esac
}

smoke_hostname_is_routed() {
  local route
  for route in $(split_csv_lines "$TUNNEL_HOSTNAMES"); do
    [ -n "$route" ] || continue
    if hostname_matches_pattern "$SMOKE_HOSTNAME" "$route"; then
      return 0
    fi
  done
  return 1
}

upsert_cname_record() {
  local hostname zone_id response existing_id existing_content existing_proxied existing_ttl desired payload
  hostname=$1
  zone_id=$(zone_id_for_hostname "$hostname")
  desired=$(jq -cn --arg name "$hostname" --arg content "$TUNNEL_TARGET" '{type: "CNAME", name: $name, content: $content, proxied: true, ttl: 1}')
  response=$(cf_api GET "/zones/$zone_id/dns_records?type=CNAME&name=$hostname")
  existing_id=$(printf '%s' "$response" | jq -r '.result[0].id // empty')
  existing_content=$(printf '%s' "$response" | jq -r '.result[0].content // empty')
  existing_proxied=$(printf '%s' "$response" | jq -r '.result[0].proxied // false')
  existing_ttl=$(printf '%s' "$response" | jq -r '.result[0].ttl // 0')

  if [ -z "$existing_id" ]; then
    log "Creating DNS route for $hostname"
    cf_api POST "/zones/$zone_id/dns_records" "$desired" >/dev/null
    return 0
  fi

  if [ "$existing_content" = "$TUNNEL_TARGET" ] && [ "$existing_proxied" = "true" ] && [ "$existing_ttl" = "1" ]; then
    log "DNS route already correct for $hostname"
    return 0
  fi

  payload=$(printf '%s' "$desired" | jq --arg id "$existing_id" '. + {id: $id}')
  log "Updating DNS route for $hostname"
  cf_api PUT "/zones/$zone_id/dns_records/$existing_id" "$payload" >/dev/null
}

sync_tunnel_config() {
  local ingress_json body response version
  ingress_json=$(jq -cn --arg hostnames "$TUNNEL_HOSTNAMES" '
    ($hostnames
      | split(",")
      | map(gsub("^\\s+|\\s+$"; ""))
      | map(select(length > 0))
      | map({hostname: ., service: "http://caddy:80", originRequest: {}}))
      + [{service: "http_status:404"}]
  ')
  body=$(jq -cn --argjson ingress "$ingress_json" '{config: {ingress: $ingress, "warp-routing": {enabled: false}}, source: "cloudflare"}')
  response=$(cf_api PUT "/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" "$body")
  printf '%s' "$response" | jq -e '.success == true' >/dev/null
  version=$(printf '%s' "$response" | jq -r '.result.version // "n/a"')
  log "Synced tunnel ingress config (version $version)"
}

[ $# -eq 1 ] || fail "Usage: bootstrap/local/cloudflare-tunnel.sh <env-file>"

ENV_FILE=$1
CF_API_BASE=${CF_API_BASE:-https://api.cloudflare.com/client/v4}

require_command curl
require_command jq
load_env_file "$ENV_FILE"
require_var CF_API_TOKEN
require_var CF_ACCOUNT_ID
require_var CF_ZONE_MAP
require_var TUNNEL_ID
require_var TUNNEL_HOSTNAMES
require_var SMOKE_HOSTNAME

smoke_hostname_is_routed || fail "SMOKE_HOSTNAME must be covered by TUNNEL_HOSTNAMES"
TUNNEL_TARGET="$TUNNEL_ID.cfargotunnel.com"

sync_tunnel_config

for hostname in $(split_csv_lines "$TUNNEL_HOSTNAMES"); do
  [ -n "$hostname" ] || continue
  upsert_cname_record "$hostname"
done
