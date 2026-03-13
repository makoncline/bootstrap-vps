# Bootstrap Details

This repo bootstraps a fresh Hetzner Ubuntu 24.04 host from this Mac over the existing `root` SSH key, installs base packages, applies Ubuntu package upgrades, performs one automatic reboot if required, enables unattended security updates, installs Docker with log rotation, installs Tailscale, configures a minimal `zsh` environment for the admin user, starts internal-only Caddy plus `cloudflared` in Docker, syncs a dedicated Cloudflare Tunnel and DNS routes for one or more domains, installs a lightweight healthcheck timer with optional Telegram alerts, and then disables all public ingress. The server is reached over Tailscale SSH; public `22`, `80`, and `443` stay closed.

## Files

- `bootstrap/hosts/.env.example`: generic template for host-specific configuration.
- `bootstrap/hosts/.env.production`: ignored working copy for the server you are bootstrapping.
- `bootstrap/local/bootstrap-host.sh`: main entrypoint from this Mac.
- `bootstrap/local/reconcile-host.sh`: reruns the idempotent remote converger on an existing host over Tailscale SSH.
- `bootstrap/local/configure-config-sync.sh`: registers a named config-sync target for the managed webhook on an existing host.
- `bootstrap/local/configure-deploy-target.sh`: registers a named deploy target for the managed webhook on an existing host.
- `bootstrap/local/prune-deploy-images.sh`: prunes old local images for a named deploy target on an existing host over Tailscale SSH.
- `bootstrap/local/restart-proxy.sh`: restarts the managed Caddy proxy stack on an existing host so new route files take effect.
- `bootstrap/local/cloudflare-tunnel.sh`: syncs remote-managed tunnel ingress and DNS routes through the Cloudflare API.
- `bootstrap/local/codex-login.sh`: opens an interactive Codex login session over Tailscale SSH on the server.
- `bootstrap/local/test-deploy-webhook.sh`: calls the managed deploy webhook for a named target and image tag.
- `bootstrap/local/test-config-sync-webhook.sh`: calls the managed config-sync webhook for a named target and git ref.
- `bootstrap/local/sync-local-codex-home.sh`: syncs repo-managed global Codex defaults, including `AGENTS.md`, `machine-notes.md`, and the default Codex config, onto this machine.
- `bootstrap/local/test-telegram-alert.sh`: sends a Telegram test message through the server's alert script.
- `bootstrap/local/sync-local-codex-skills.sh`: syncs repo-managed Codex skills into this machine's global Codex skills directory.
- `bootstrap/local/sync-codex-skills.sh`: syncs repo-managed Codex skills to an already-bootstrapped server.
- `bootstrap/remote/init-host.sh`: remote converger that prepares the host and starts the tunnel, proxy, and sample app stacks.
- `bootstrap/remote/lockdown-ssh.sh`: remote hardening step that leaves only Tailscale ingress open.

## Required inputs

- `TUNNEL_TOKEN`: token copied from the new tunnel you create for this server.
- `TUNNEL_ID`: UUID of that tunnel.
- `CF_ACCOUNT_ID`: Cloudflare account ID that owns the tunnel and the zones.
- `CF_API_TOKEN`: API token with `Cloudflare Tunnel Edit` and `DNS Edit` for the relevant account/zones.
- `CF_ZONE_MAP`: comma-separated `zone-name:zone-id` entries, for example `makon.dev:abc123,daylilycatalog.com:def456`.
- `TUNNEL_HOSTNAMES`: comma-separated public hostnames or wildcard hostnames routed to this tunnel, for example `*.makon.dev,*.daylilycatalog.com`.
- `SMOKE_HOSTNAME`: one specific hostname covered by `TUNNEL_HOSTNAMES` and routed by the sample Caddy config, for example `whoami.makon.dev`.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: optional Telegram credentials for health alerts.
- `HEALTHCHECK_DISK_PCT`: optional disk usage alert threshold for `/`, default `85`.
- `DEPLOY_WEBHOOK_HOSTNAME` and `DEPLOY_WEBHOOK_TOKEN`: optional deploy webhook hostname and shared secret. The hostname should usually already be covered by `TUNNEL_HOSTNAMES`.

## Usage

1. In Cloudflare Zero Trust, create a new tunnel for this server and copy its token and tunnel ID.
2. Copy `bootstrap/hosts/.env.example` to `bootstrap/hosts/.env.production`.
3. Fill in the public IP, Tailscale auth key, tunnel token and ID, Cloudflare account and zone IDs, and the hostnames or wildcards you want routed through this server.
4. Make sure this Mac is connected to the same Tailscale tailnet before running the bootstrap.
5. Run:

```bash
./bootstrap/local/bootstrap-host.sh bootstrap/hosts/.env.production
```

Example:

```bash
cp bootstrap/hosts/.env.example bootstrap/hosts/.env.production
```

On a fresh Ubuntu image, the bootstrap may reboot the host once after package upgrades and then resume automatically.

To apply updated bootstrap logic to an already-bootstrapped host over Tailscale:

```bash
./bootstrap/local/reconcile-host.sh bootstrap/hosts/.env.production
```

## Optional toggles

- `BOOTSTRAP_SKIP_CLOUDFLARE=1`: skip tunnel ingress and DNS sync.
- `BOOTSTRAP_SKIP_SMOKE_CHECK=1`: skip the HTTPS smoke check.
- `BOOTSTRAP_SKIP_LOCKDOWN=1`: leave public SSH enabled for a manual troubleshooting pass.
- `BOOTSTRAP_SSH_KEY=/path/to/key`: override the default SSH key path.

## End state

- `makon` exists as a passwordless sudo user with the same admin key installed.
- `makon` uses `zsh` as the login shell with packaged autosuggestions and syntax highlighting plus a minimal managed `~/.zshrc.d/bootstrap.zsh`.
- Tailscale is up with SSH enabled and the host named from `TAILSCALE_HOSTNAME`.
- unattended security updates are enabled for Ubuntu packages.
- Docker Engine and the Compose plugin are installed and enabled.
- Docker uses local log rotation with `max-size=10m` and `max-file=3`.
- The host has a `1G` swapfile at `/swapfile` with `vm.swappiness=10` to absorb short-lived memory spikes from interactive tooling.
- Node.js, npm, and Codex CLI are installed on the server.
- Cursor's `cursor-sandbox-apparmor` package is installed and its remote profile is patched with `network netlink raw` so Cursor remote terminals work on Ubuntu 24.04 AppArmor hosts.
- repo-managed global Codex defaults are installed under `~/.codex/AGENTS.md`, `~/.codex/memories/machine-notes.md`, and a default `~/.codex/config.toml` if one does not already exist.
- Repo-managed Codex skills are installed under `~/.codex/skills`.
- `/srv/stacks/proxy` runs internal-only Caddy on the shared `edge` network.
- `/srv/stacks/tunnel` runs `cloudflared` with the dedicated server tunnel token.
- `/srv/stacks/whoami` runs a sample backend on the shared `edge` network.
- a systemd timer runs a lightweight healthcheck every 5 minutes against Docker, Tailscale, disk usage, and `https://<SMOKE_HOSTNAME>`.
- if Telegram credentials are present, healthcheck failures and recoveries are sent to that chat.
- `notify "message"` is available for the admin user on the server and sends a one-off Telegram alert without `sudo`.
- if deploy webhook credentials are present, a small authenticated webhook service runs on the host and is reverse proxied through the managed Caddy stack.
- Cloudflare wildcard or exact DNS CNAMEs point at `<TUNNEL_ID>.cfargotunnel.com`.
- Public SSH and all other public ingress are disabled once Tailscale SSH is verified.

## Codex CLI

The bootstrap installs Codex CLI using `npm i -g @openai/codex`, matching the official OpenAI CLI setup docs.

After bootstrap, authenticate interactively over Tailscale:

```bash
./bootstrap/local/codex-login.sh bootstrap/hosts/.env.production
```

The helper uses `codex login --device-auth`, which is the headless-friendly device-code flow for a server session. Source: [Codex CLI setup](https://developers.openai.com/codex/cli).

Fresh servers also get a minimal default `~/.codex/config.toml` if none exists yet, with:

- built-in web search enabled
- workspace-write network access enabled

Repo-managed server skills are bundled from `bootstrap/codex-skills` during bootstrap. To push skill updates to an existing host later:

```bash
./bootstrap/local/sync-codex-skills.sh bootstrap/hosts/.env.production
```

Current repo-managed server skills include:

- `server-new-app`: add or update an app under `/srv/stacks`
- `server-deploy-prep`: prepare an app repo for this VPS pattern
- `server-state-audit`: inspect server health, Docker disk usage, logs, and cleanup opportunities

To install the same repo-managed skills as global Codex skills on this machine:

```bash
./bootstrap/local/sync-local-codex-skills.sh
```

To install the same repo-managed global Codex defaults on this machine:

```bash
./bootstrap/local/sync-local-codex-home.sh
```

To test Telegram alerts after bootstrap:

```bash
./bootstrap/local/test-telegram-alert.sh bootstrap/hosts/.env.production
```

On the server itself, the admin user can send a one-off message directly:

```bash
notify "deploy finished"
```

To register a deploy target for an app stack:

```bash
./bootstrap/local/configure-deploy-target.sh bootstrap/hosts/.env.production daylilycatalog /srv/stacks/daylilycatalog https://vps-test.daylilycatalog.com
```

Optional environment overrides for deploy targets:

- `DEPLOY_IMAGE_REPOSITORY=ghcr.io/makoncline/daylilycatalog`
- `DEPLOY_KEEP_IMAGE_COUNT=2`

If both are set, the server will prune old local app images after a successful deploy and keep only the newest configured count, always including the currently deployed tag.

To register a config-sync target for app-owned stack files:

```bash
./bootstrap/local/configure-config-sync.sh bootstrap/hosts/.env.production daylilycatalog https://github.com/makoncline/new-daylily-catalog.git deploy/vps /srv/stacks/daylilycatalog https://vps-test.daylilycatalog.com
```

To test the managed deploy webhook once a target exists:

```bash
./bootstrap/local/test-deploy-webhook.sh bootstrap/hosts/.env.production daylilycatalog main-deadbeef
```

To prune old local images for a deploy target manually:

```bash
./bootstrap/local/prune-deploy-images.sh bootstrap/hosts/.env.production daylilycatalog 2
```

To test the managed config-sync webhook once a target exists:

```bash
./bootstrap/local/test-config-sync-webhook.sh bootstrap/hosts/.env.production daylilycatalog <git-sha>
```

The webhook accepts `POST https://<DEPLOY_WEBHOOK_HOSTNAME>/deploy/<target>` with:

- `Authorization: Bearer <DEPLOY_WEBHOOK_TOKEN>`
- JSON body like `{"image_tag":"main-deadbeef","clear_cache":false}`

The host-side deploy runner updates `IMAGE_TAG`, runs `docker compose pull && docker compose up -d`, smoke-checks the configured URL, and sends Telegram notifications that include the previous image tag, attempted image tag, and rollback status.

The same service also accepts `POST https://<DEPLOY_WEBHOOK_HOSTNAME>/sync-config/<target>` with:

- `Authorization: Bearer <DEPLOY_WEBHOOK_TOKEN>`
- JSON body like `{"git_ref":"<commit-sha>"}`.

For config sync, the server keeps a dedicated checkout under `/var/lib/bootstrap-config-sync/<target>`, fetches that exact ref, copies the configured app-owned files into the live stack locations, restarts the shared proxy if the route changed, reapplies the app stack if the Compose file changed, and leaves the live `.env` untouched.

When you add or update route files under `/srv/stacks/proxy/sites`, apply them by restarting the managed Caddy container instead of using `caddy reload`:

```bash
./bootstrap/local/restart-proxy.sh bootstrap/hosts/.env.production
```

This bootstrap sets `admin off` in the managed Caddyfile, so the admin API reload endpoint is intentionally unavailable.

## Manual GitHub Setup

GitHub SSH auth is intentionally not part of bootstrap. If the server needs to clone or push repos directly:

1. Set git identity on the server:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

2. Generate a dedicated SSH key on the server, for example:

```bash
ssh-keygen -t ed25519 -C "makon-dev-0 github" -f ~/.ssh/github_ed25519
```

3. Add the public key to GitHub.
4. Add a host stanza in `~/.ssh/config` if you want to force that key for `github.com`.
5. Verify with:

```bash
ssh -T git@github.com
```
