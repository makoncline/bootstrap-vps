---
name: server-new-app
description: Guide for adding or updating an app on a server bootstrapped by this repo. Use when asked to deploy a new app, add a hostname, create a Docker Compose stack under /srv/stacks, wire a service into the shared Caddy proxy, or explain how apps should be hosted on this server.
---

# Server New App

This skill applies to servers bootstrapped by this repo.

## Assumptions

- App stacks live in `/srv/stacks/<app>`.
- Caddy runs from `/srv/stacks/proxy`.
- Host routes live in `/srv/stacks/proxy/sites/*.caddy`.
- All app containers join the external Docker network `edge`.
- `cloudflared` forwards public traffic to Caddy; apps should not publish ports publicly.

## Workflow

1. Inspect `/srv/stacks/proxy/sites` and existing app stacks before choosing names.
2. Pick a hostname already covered by the configured tunnel wildcards when possible.
3. Create `/srv/stacks/<app>/compose.yaml` with `restart: unless-stopped` and the `edge` network.
4. Add a Caddy route file in `/srv/stacks/proxy/sites/<nn>-<app>.caddy` that matches the hostname and reverse proxies to the service name and internal port.
5. Start or update the app with `docker compose -f /srv/stacks/<app>/compose.yaml up -d`.
6. Reload Caddy with `docker compose -f /srv/stacks/proxy/compose.yaml exec caddy caddy reload --config /etc/caddy/Caddyfile`.
7. Verify with:
   - `docker compose -f /srv/stacks/<app>/compose.yaml ps`
   - `curl -I https://<hostname>`

## Compose Pattern

```yaml
services:
  app:
    image: ghcr.io/example/app:latest
    restart: unless-stopped
    networks:
      - edge

networks:
  edge:
    external: true
```

If the app listens on a non-default port, keep that internal and point Caddy at it. Do not add public `ports:` unless there is a specific operational reason.

## Caddy Pattern

```caddy
@app host app.example.com
handle @app {
  reverse_proxy app:3000
}
```

Use the Compose service name as the upstream host on the `edge` network.

## Domain Changes

- If the hostname fits an existing wildcard such as `*.makon.dev`, no Cloudflare change is needed.
- If a new domain or hostname falls outside the configured tunnel hostnames, update `bootstrap/hosts/.env.production` in this repo and rerun `bootstrap/local/cloudflare-tunnel.sh` from the local machine.

## Validation

- Confirm the app container is healthy.
- Confirm Caddy reload succeeds.
- Confirm the public HTTPS hostname responds through Cloudflare Tunnel.
- If changing an existing app, check for route conflicts in `/srv/stacks/proxy/sites`.
