# Bootstrap Details

This repo bootstraps a fresh Hetzner Ubuntu 24.04 host from this Mac over the existing `root` SSH key, installs base packages, applies Ubuntu package upgrades, performs one automatic reboot if required, enables unattended security updates, installs Docker with log rotation, installs Tailscale, configures a minimal `zsh` environment for the admin user, starts internal-only Caddy plus `cloudflared` in Docker, syncs a dedicated Cloudflare Tunnel and DNS routes for one or more domains, installs a lightweight healthcheck timer with optional Telegram alerts, and then disables all public ingress. The server is reached with OpenSSH over Tailscale; public `22`, `80`, and `443` stay closed.

## Files

- `bootstrap/hosts/.env.example`: generic template for host-specific configuration.
- `bootstrap/hosts/.env.production`: ignored working copy for the server you are bootstrapping.
- `bootstrap/local/bootstrap-host.sh`: main entrypoint from this Mac.
- `bootstrap/local/reconcile-host.sh`: reruns the idempotent remote converger on an existing host with OpenSSH over Tailscale.
- `bootstrap/local/configure-config-sync.sh`: registers a named config-sync target for the managed webhook on an existing host.
- `bootstrap/local/configure-deploy-target.sh`: registers a named deploy target for the managed webhook on an existing host.
- `bootstrap/local/prune-deploy-images.sh`: prunes old local images for a named deploy target on an existing host with OpenSSH over Tailscale.
- `bootstrap/local/restart-proxy.sh`: restarts the managed Caddy proxy stack on an existing host so new route files take effect.
- `bootstrap/local/cloudflare-tunnel.sh`: syncs remote-managed tunnel ingress and DNS routes through the Cloudflare API.
- `bootstrap/local/codex-login.sh`: opens an interactive Codex login session with OpenSSH over Tailscale on the server.
- `bootstrap/local/test-deploy-webhook.sh`: calls the managed deploy webhook for a named target and image tag.
- `bootstrap/local/test-config-sync-webhook.sh`: calls the managed config-sync webhook for a named target and git ref.
- `bootstrap/local/sync-local-codex-home.sh`: syncs repo-managed global Codex defaults, including `AGENTS.md`, `machine-notes.md`, and the default Codex config, onto this machine.
- `bootstrap/local/test-telegram-alert.sh`: sends a Telegram test message through the server's alert script.
- `bootstrap/local/sync-local-codex-skills.sh`: syncs repo-managed Codex skills into this machine's global Codex skills directory.
- `bootstrap/local/sync-codex-skills.sh`: syncs repo-managed Codex skills to an already-bootstrapped server.
- `bootstrap/remote/init-host.sh`: remote converger that prepares the host and starts the tunnel, proxy, and sample app stacks.
- `bootstrap/remote/lockdown-ssh.sh`: remote hardening step that leaves only Tailscale ingress open.

## Required inputs

- `ADMIN_USER`: admin account used by all local SSH helpers. Default: `admin`. For an existing host, set this to its current account in the host env file before reconciliation.
- `BOOTSTRAP_SSH_KEY`: path to the SSH private key. Set this environment variable before bootstrap or reconciliation.

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
BOOTSTRAP_SSH_KEY=/path/to/key ./bootstrap/local/bootstrap-host.sh bootstrap/hosts/.env.production
```

Example:

```bash
cp bootstrap/hosts/.env.example bootstrap/hosts/.env.production
```

On a fresh Ubuntu image, the bootstrap may reboot the host once after package upgrades and then resume automatically.

To apply updated bootstrap logic to an already-bootstrapped host over Tailscale:

```bash
BOOTSTRAP_SSH_KEY=/path/to/key ./bootstrap/local/reconcile-host.sh bootstrap/hosts/.env.production
```

## Optional toggles

- `BOOTSTRAP_SKIP_CLOUDFLARE=1`: skip tunnel ingress and DNS sync.
- `BOOTSTRAP_SKIP_SMOKE_CHECK=1`: skip the HTTPS smoke check.
- `BOOTSTRAP_SKIP_LOCKDOWN=1`: leave public SSH enabled for a manual troubleshooting pass.

## End state

- The configured admin account exists as a passwordless sudo user with the same admin key installed.
- The configured admin account uses `zsh` as the login shell with packaged autosuggestions and syntax highlighting plus a minimal managed `~/.zshrc.d/bootstrap.zsh`.
- Tailscale is up with Tailscale SSH disabled and the host named from `TAILSCALE_HOSTNAME`.
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
- Public SSH and all other public ingress are disabled once OpenSSH over Tailscale is verified.

## Codex CLI

The bootstrap installs Codex CLI using `npm i -g @openai/codex`, matching the official OpenAI CLI setup docs.

For direct ChatGPT access, authenticate interactively over Tailscale after bootstrap:

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

For config sync, the server keeps a dedicated checkout under `/var/lib/bootstrap-config-sync/<target>`, fetches that exact ref, validates the app-owned files, reapplies changed Compose configuration, activates and checks the proxy route, and leaves the live `.env` untouched.

## AI proxy over Tailscale

Set `AI_PROXY_URL` and `AI_PROXY_MODEL` in the private host env file to use CLIProxy on another tailnet machine. Both settings are blank in the example file. Use the full MagicDNS name ending in `.ts.net`, a Tailscale IP, or loopback for a local test. The URL must end in `/v1`. Select an exact model ID from that proxy's `/v1/models` endpoint.

During bootstrap or reconcile, these settings select the `cli_proxy` provider and model in the admin user's `~/.codex/config.toml`. Other settings, comments, and config symlinks stay in place. TOML Kit handles the config update; Ubuntu installs it as `python3-tomlkit`. The provider uses HTTP Responses streaming and sends no client API key. It does not use Codex login credentials. This follows the [Codex custom provider settings](https://learn.chatgpt.com/docs/config-file/config-reference).

Leave both fields blank to keep the current Codex config. Blank fields do not remove a proxy that was already configured. To stop using that proxy, edit the user's Codex config explicitly. Partial or invalid settings stop the local helper before SSH starts. Setup does not require the proxy to be online.

On a configured server, run as the admin user:

```bash
bootstrap-ai-proxy check
```

This checks the model list with a five-second transfer limit and a 1 MiB response limit. It does not load curl settings, send credentials, use HTTP proxies, or follow redirects. It reports `inference_verified: false`: a model list does not prove that upstream authorization works. To test a real request, run `codex exec --ephemeral --skip-git-repo-check --sandbox read-only 'Reply exactly PROXY_OK. Do not use tools.'` in an empty test directory. This request uses the proxy's upstream account.

On the proxy machine, bind inference to its Tailscale IP and allow only the required clients and port in the [tailnet grants](https://tailscale.com/docs/reference/syntax/policy-file). Keep the listener off the public network. HTTP over the Tailscale address uses the encrypted tailnet connection. For Docker, publish the port on that Tailscale IP, not on all host interfaces.

For [CLIProxyAPI v8.0.45](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.45/config.example.yaml), use `access.api-keys: []` for no extra client token. Keep `management.secret-key` empty and the control panel disabled. Do not supply a management password through the environment or TUI. Set `server.commercial-mode: true` to omit the detailed request logging middleware; `observability.logs.request-log: false` alone still permits failed-request body capture in this version. Keep debug and profiling off. Configure upstream provider access on that machine. These client settings do not copy subscription credentials to the VPS or configure other AI clients.

To test on a Mac without changing its normal Codex home:

```bash
# Install TOML Kit in a temporary Python virtual environment first.
export CODEX_HOME="$(mktemp -d)"
AI_PROXY_URL=http://127.0.0.1:8317/v1 AI_PROXY_MODEL='replace-with-model-id' \
  python3 bootstrap/remote/ai-proxy.py configure
python3 bootstrap/remote/ai-proxy.py check
# Run the Codex smoke request above in an empty directory.
```

Use this temporary `CODEX_HOME` only in the test shell. Keep the real proxy URL and model in private host settings. Applying these changes to an existing VPS remains a separate operation.

## Proxy operations

Use the same confirmed activation path as config sync:

```bash
sudo /usr/local/bin/bootstrap-apply-proxy --smoke-url https://app.example.com /path/to/new-route.caddy /srv/stacks/proxy/sites/20-example.caddy
```

The command serializes updates, validates the candidate, waits for Caddy to confirm its reload, and checks the route. On failure, it restores and reactivates the previous route. Without source and destination arguments, it validates and activates the current configuration.

The managed image starts Caddy as PID 1 with `caddy run` and a config file. This [signal reload](https://caddyserver.com/docs/command-line#signals) works with `admin off`. Do not use it with `--resume` or after API config changes. Do not use `docker kill` for reload signals: it can set Docker's manual-stop flag even when Caddy keeps running ([Moby #41302](https://github.com/moby/moby/issues/41302)). Check the Caddy logs and an uncached route after the signal; signal delivery does not confirm that the reload succeeded.

The proxy pins Caddy 2.11.7 and pulls that image before startup. Signal reload requires Caddy 2.11 or later. The proxy keeps `restart: unless-stopped`, so a deliberate stop remains in effect after Docker restarts. Start it again with `docker compose -f /srv/stacks/proxy/compose.yaml up -d`.

Access logs are stored in `/srv/stacks/proxy/logs/access-retained.log`. Caddy rotates them at 20 MiB and keeps at most eight compressed archives, with a 168-hour archive age limit. Cleanup runs when Caddy creates a log file. Retained time depends on traffic; these limits do not guarantee seven days of history. The active file is not subject to the archive age limit. The filter removes query contents and these request headers: Authorization, Proxy-Authorization, X-Deploy-Token, Cookie, Set-Cookie, and Referer. It keeps request paths, status codes, and timing.

Runtime logs omit request objects, including their raw URI and headers. They retain the error cause, status, and timing. This also covers reverse-proxy failures before an upstream responds.

Changes to [file output options](https://caddyserver.com/docs/caddyfile/directives/log#file) for the same filename need a restart. Use the existing helper:

```bash
./bootstrap/local/restart-proxy.sh bootstrap/hosts/.env.production
```

## Tests

Run `./tests/run.sh` for the shell and Python tests. Python needs TOML Kit (`sudo apt-get install python3-tomlkit` on Ubuntu, or install `tomlkit` in a Python virtual environment on macOS). Run `python3 tests/test_proxy.py` with a local Docker context for the container integration test. The test creates and removes its own Compose project. It checks the generated configuration, redaction, signal reload, and restart state. It does not restart the Docker daemon or test retention over seven days.

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

## OpenSSH access and existing hosts

Bootstrap uses the configured admin public key. It preserves other authorized keys and existing options on a matching key. To limit the clients that can use a key, add a `from="<client-tailscale-ip>"` option to that key in the admin user's `~/.ssh/authorized_keys`. Reconciliation preserves that restriction. Keep a separate recovery method.

Use a client SSH config entry with `HostName <server-tailscale-name>`, `User <admin-user>`, `IdentityFile <private-key>`, `IdentitiesOnly yes`, and `ForwardAgent no`. Verify the server's OpenSSH host-key fingerprint through a trusted channel before accepting it. Bootstrap uses first-use host-key acceptance and rejects changed keys; verify both the public and Tailscale host keys independently.

Before you transition an existing host, set `ADMIN_USER` in its host env file to the current admin account. This sets the account for reconciliation and all follow-up helpers. Then use the console for this transition:

1. Check the admin public key and its restrictions. Run `sudo sshd -t` and confirm that OpenSSH is running. Record its host-key fingerprint.
2. Run `sudo tailscale set --ssh=false` in the console.
3. From a separate client, verify the fingerprint, make a new key-authenticated OpenSSH connection over Tailscale, and run `sudo -n true`.
4. Keep console access until these checks pass. If a check fails, repair OpenSSH or restore Tailscale SSH from the console. Run reconciliation only after the checks pass.

Tailscale SSH and OpenSSH use different host keys. Verify the new fingerprint before removing the old entry. See [Tailscale's transition guidance](https://tailscale.com/docs/features/tailscale-ssh#disable-tailscale-ssh).

## Host and action logs

Host journals persist across boots, with a 256 MiB budget, a 1 GiB free-space reserve, and a seven-day maximum age. Available history can be shorter under load. Docker keeps its existing rotated JSON logs. Caddy access logs use the separate bounded file described above.

Deploy and config-sync commands run through `bootstrap-run-action`. It records an action ID, target, phase, timestamps, exit status, and up to 1 MiB of sanitized output in the journal under `bootstrap-action`. Part of that budget is reserved for the final result, including the output-truncation flag. Deployment commands have a ten-minute deadline, with a five-minute new-version phase and a separate two-minute rollback budget. Rollback uses the cached previous image without a registry pull or build. Setup and reconciliation have a thirty-minute deadline. The latest result is stored in `/var/lib/bootstrap/last-action.json`; unavailable journals and truncated output are marked. Webhook errors return the action ID rather than raw command output. Do not print environment files or credentials from custom deploy commands. Git errors reach the same bounded logger. Redaction covers known host secrets, common credential fields, and HTTP URL credentials; it cannot make arbitrary application output safe to share.

Health checks save the current result independently from the last delivered alert. A failed Telegram request is bounded and retried on the next health check.

## Agent diagnostics

Run these on a host you intend to inspect:

```bash
sudo bootstrapctl status
sudo bootstrapctl logs actions
sudo bootstrapctl logs proxy
sudo bootstrapctl diagnose
```

`status` reports the applied bootstrap revision, setup/action results, service state, tool versions, running images and log settings, health, resource pressure, and log locations. `logs` selects a host journal, action log, health log, access log, or named stack. `diagnose` combines status with recent host, action, health, proxy, and tunnel logs. It limits queries to recent records, uses a 60-second collection budget, and caps the final JSON report at 1 MiB. Missing, denied, timed-out, and truncated data are marked.

Setup and reconciliation retain their last step and result in `/var/lib/bootstrap/last-setup.json`. An expected package reboot is recorded as `reboot_required`. Diagnostics identify a running record whose process or boot no longer matches as interrupted. The manifest in `/etc/bootstrap/manifest.json` records the applied revision; local bundles mark tracked uncommitted source changes as dirty. The local workflows also verify Codex through the actual non-interactive SSH path.

The command uses the existing admin account and sudo. It makes no repairs. Review logs before sharing them. A complete host reboot has not been tested by the container tests; those tests cover fresh SSH access, a simulated private interface, proxy activation, and journal persistence across container replacement.
