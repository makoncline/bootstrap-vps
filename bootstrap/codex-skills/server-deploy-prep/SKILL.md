---
name: server-deploy-prep
description: Guide for preparing an app repository so it can be deployed onto a server bootstrapped by this repo. Use when asked to make an app deployable on this Docker plus Caddy plus Cloudflare Tunnel server pattern, add Docker assets, define runtime environment needs, or explain the constraints an app must satisfy before it can be hosted here.
---

# Server Deploy Prep

Use this skill before the app reaches the server. It is about making an app repo deployable into this hosting pattern.

## Target Platform

- Apps run in Docker on a single VPS.
- Public traffic enters through Cloudflare Tunnel, then internal Caddy, then the app container.
- The app should not bind public host ports.
- Runtime stacks on the server live under `/srv/stacks/<app>`.
- App containers must join the shared Docker network `edge`.

## Goal

Leave the app repo with everything needed so the server-side step is mechanical:

1. build or pull an image
2. provide env vars and secrets
3. run a Compose stack on `edge`
4. add one Caddy hostname route

## App Repo Requirements

### Containerization

- Add a production Dockerfile.
- Keep the image single-purpose and reproducible.
- Expose the app's internal port in the container, but do not rely on publishing it publicly.
- Prefer one main process per container.

### Runtime contract

- Document the internal listening port.
- Document required environment variables and which are secrets.
- Ensure the app binds to `0.0.0.0`, not just localhost inside the container.
- Ensure health or readiness behavior is obvious enough to validate after deploy.

### Storage and state

- Call out any persistent disk requirements.
- Call out any external services required: database, object storage, queue, Redis, third-party APIs.
- If local persistence is needed, specify the exact volume mounts required in Compose.

### Networking

- Assume traffic arrives via reverse proxy with the correct `Host` header.
- Make sure the app works when served behind HTTPS termination at Caddy.
- If the framework needs trusted proxy settings, configure them explicitly.

## Deliverables To Add In The App Repo

- `Dockerfile`
- `.env.example` or equivalent runtime env template
- deployment notes with:
  - image build or publish command
  - internal port
  - required env vars
  - required volumes
  - required external dependencies
- if appropriate, a server-targeted `compose.yaml` snippet or example

## Server Compose Pattern

Use this pattern when generating deployment instructions for the app:

```yaml
services:
  app:
    image: ghcr.io/example/app:latest
    restart: unless-stopped
    env_file:
      - .env
    networks:
      - edge

networks:
  edge:
    external: true
```

Only add `volumes:` or other services when the app actually needs them.

## Caddy Route Pattern

Use this route shape when describing the matching server config:

```caddy
@app host app.example.com
handle @app {
  reverse_proxy app:3000
}
```

The upstream host should match the Compose service name and internal app port.

## Checklist Before Declaring The App Deployable

- The app builds successfully in Docker.
- The app listens on a known internal port on `0.0.0.0`.
- Required env vars are documented.
- Secret vs non-secret config is clear.
- Any persistent storage requirements are explicit.
- Reverse proxy behavior is accounted for.
- The server-side Compose and Caddy snippets can be written without guessing.

## Handoff

When finishing, provide:

- the app's internal port
- the required env vars
- any required volumes
- the exact Compose service block
- the exact Caddy route block
- any follow-up Cloudflare change only if the hostname falls outside existing tunnel wildcards
