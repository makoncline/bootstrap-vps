#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP_DIR="$REPO_ROOT/tests/.tmp/cloudflare-tunnel"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

PORT=$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)

python3 "$REPO_ROOT/tests/fixtures/mock_cloudflare.py" "$PORT" >"$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT
sleep 1

cat >"$TMP_DIR/host.env" <<'EOF'
CF_ACCOUNT_ID=acct-test
CF_API_TOKEN=token-test
CF_ZONE_MAP=makon.dev:zone-test,daylilycatalog.com:zone-other
TUNNEL_ID=11111111-2222-3333-4444-555555555555
TUNNEL_HOSTNAMES=*.makon.dev,*.daylilycatalog.com
SMOKE_HOSTNAME=whoami.makon.dev
EOF

CF_API_BASE="http://127.0.0.1:$PORT/client/v4" \
  "$REPO_ROOT/bootstrap/local/cloudflare-tunnel.sh" "$TMP_DIR/host.env"
CF_API_BASE="http://127.0.0.1:$PORT/client/v4" \
  "$REPO_ROOT/bootstrap/local/cloudflare-tunnel.sh" "$TMP_DIR/host.env"

records=$(curl --silent "http://127.0.0.1:$PORT/dump")
printf '%s' "$records" | jq -e '.records | length == 2' >/dev/null
printf '%s' "$records" | jq -e '.records[] | select(.type == "CNAME" and .name == "*.makon.dev" and .content == "11111111-2222-3333-4444-555555555555.cfargotunnel.com" and .proxied == true and .ttl == 1)' >/dev/null
printf '%s' "$records" | jq -e '.records[] | select(.type == "CNAME" and .name == "*.daylilycatalog.com" and .content == "11111111-2222-3333-4444-555555555555.cfargotunnel.com" and .proxied == true and .ttl == 1)' >/dev/null
