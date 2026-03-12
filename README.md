# servers

Bootstrap for a fresh Hetzner Ubuntu 24.04 VPS using:

- Tailscale SSH for admin access
- Docker Compose for app stacks
- Caddy as the internal reverse proxy
- Cloudflare Tunnel for public ingress
- unattended security updates and Docker log rotation
- a lightweight healthcheck timer with optional Telegram alerts
- an optional authenticated deploy webhook for app repos
- a minimal interactive `zsh` setup for the admin user
- Codex CLI plus repo-managed skills on the server

## What it does

The bootstrap starts from temporary public `root` SSH, installs the base server, applies Ubuntu package upgrades, performs one automatic reboot if the host requires it, joins Tailscale, brings up `cloudflared`, Caddy, and a sample app, verifies Tailscale SSH from this Mac, then disables public SSH.

## Main commands

```bash
cp bootstrap/hosts/.env.example bootstrap/hosts/.env.production
./bootstrap/local/bootstrap-host.sh bootstrap/hosts/.env.production
./bootstrap/local/codex-login.sh bootstrap/hosts/.env.production
```

Useful follow-ups:

```bash
./bootstrap/local/reconcile-host.sh bootstrap/hosts/.env.production
./bootstrap/local/configure-deploy-target.sh bootstrap/hosts/.env.production daylilycatalog /srv/stacks/daylilycatalog https://vps-test.daylilycatalog.com
./bootstrap/local/configure-config-sync.sh bootstrap/hosts/.env.production daylilycatalog https://github.com/makoncline/new-daylily-catalog.git deploy/vps /srv/stacks/daylilycatalog https://vps-test.daylilycatalog.com
./bootstrap/local/restart-proxy.sh bootstrap/hosts/.env.production
./bootstrap/local/sync-codex-skills.sh bootstrap/hosts/.env.production
./bootstrap/local/sync-local-codex-home.sh
./bootstrap/local/sync-local-codex-skills.sh
./bootstrap/local/test-telegram-alert.sh bootstrap/hosts/.env.production
./bootstrap/local/test-deploy-webhook.sh bootstrap/hosts/.env.production daylilycatalog main-deadbeef
./bootstrap/local/test-config-sync-webhook.sh bootstrap/hosts/.env.production daylilycatalog <git-sha>
```

When you add or change Caddy route files under `/srv/stacks/proxy/sites` on the server, use `bootstrap/local/restart-proxy.sh`. This bootstrap runs Caddy with the admin API disabled, so `caddy reload` is not the right apply path.

If you enable `DEPLOY_WEBHOOK_HOSTNAME` and `DEPLOY_WEBHOOK_TOKEN` in [`bootstrap/hosts/.env.example`](/Users/makon/dev/servers/bootstrap/hosts/.env.example), bootstrap will also install a small authenticated deploy webhook on the server. App repos can call it after a successful image push.

That same webhook now also supports config sync from an app repo commit SHA into the live stack files, using a dedicated server-side checkout under `/var/lib/bootstrap-config-sync/<target>` instead of `git pull` inside `/srv/stacks`.

Manual one-time server setup that is not part of bootstrap:

- configure GitHub SSH access on the server if you want it to clone or push repos directly
- set `git config --global user.name` and `git config --global user.email` on the server

On the server, you can send a one-off Telegram message with:

```bash
notify "deploy finished"
```

## Docs

- Detailed bootstrap and operational notes: [bootstrap/README.md](/Users/makon/dev/servers/bootstrap/README.md)
- Manual fallback checklist: [bootstrap/OPERATIONS_PROMPT.md](/Users/makon/dev/servers/bootstrap/OPERATIONS_PROMPT.md)
- Server operator skills: [server-new-app](/Users/makon/dev/servers/bootstrap/codex-skills/server-new-app/SKILL.md), [server-deploy-prep](/Users/makon/dev/servers/bootstrap/codex-skills/server-deploy-prep/SKILL.md)
