---
name: server-state-audit
description: Assess the current health and resource state of a Linux VPS. Use when asked to audit a server, check CPU or memory or disk pressure, inspect Docker image or volume growth, review recent app or system logs, spot suspicious operational issues, or produce a concise server health report.
---

# Server State Audit

Use this skill on servers bootstrapped by this repo or on similar Linux VPS hosts.

## Goal

Produce a concise operator report covering:

- disk pressure and obvious storage growth
- Docker image, container, volume, and build-cache usage
- CPU, memory, load, and swap
- recent app and system log anomalies
- service health for Docker, Tailscale, and key app containers
- concrete cleanup or follow-up actions

## Output Shape

Keep the final report short and structured:

1. `Status`: one sentence overall assessment
2. `Findings`: only real issues or notable risks, ordered by severity
3. `Metrics`: a compact snapshot of disk, memory, load, and container state
4. `Actions`: specific next steps, including safe cleanup commands when justified

Do not dump raw logs unless they are necessary to explain a finding.

## Core Checks

Run only the checks needed to answer the request, but this is the default sweep:

### System health

- `uptime`
- `free -h`
- `df -h`
- `df -i`
- `ps -eo pid,ppid,cmd,%mem,%cpu --sort=-%cpu | head -n 15`

If available, `systemctl --failed` and targeted `systemctl status` checks are useful.

### Docker usage

- `docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.RunningFor}}'`
- `docker system df`
- `docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}'`

If disk pressure is part of the problem, also inspect:

- `docker volume ls`
- `sudo sh -c 'du -sh /var/lib/docker/* 2>/dev/null | sort -h'`

Only recommend prune commands after confirming what is safe to remove.

### Logs

Check recent logs for the app stack and host services most likely to matter:

- `docker compose -f /srv/stacks/<app>/compose.yaml logs --tail=200`
- `sudo journalctl -u docker -n 200 --no-pager`
- `sudo journalctl -p warning -n 200 --no-pager`

For this bootstrap pattern, also consider:

- `sudo journalctl -u tailscaled -n 100 --no-pager`
- `sudo journalctl -u bootstrap-healthcheck -n 100 --no-pager`
- `sudo journalctl -u bootstrap-deploy-webhook -n 100 --no-pager`

If the current user cannot read host journals or `/var/lib/docker`, note that as a visibility limit in the report instead of treating the permission error as a server problem.

### Host-specific checks for this repo's servers

- `tailscale status`
- `docker compose -f /srv/stacks/proxy/compose.yaml ps`
- `docker compose -f /srv/stacks/tunnel/compose.yaml ps`
- app stack status under `/srv/stacks/<app>`

## Cleanup Guidance

Do not run cleanup automatically unless the user asked you to change the server.

If cleanup is warranted, prefer recommending the narrowest safe command:

- stale images only: `docker image prune -a`
- build cache only: `docker builder prune`
- broader cleanup: `docker system prune`

Always say what each command would remove and when it is risky.

## Report Heuristics

- High disk usage on `/` or `/var/lib/docker` is worth calling out.
- Large numbers of dangling or old Docker images are worth calling out.
- Repeated container restarts, OOM kills, failed units, or warning-heavy logs are worth calling out.
- Low single-sample CPU is not a problem by itself; combine with load, memory, and logs.
- If nothing is wrong, say so explicitly and keep the report compact.

## Invocation Notes

When running Codex non-interactively on a server, a good pattern is:

```bash
codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -C / \
  'Use the server-state-audit skill to assess this server and produce a concise report.'
```

Use `-C /srv/stacks/<app>` instead of `/` if the request is app-specific, but for whole-host audits prefer `/`.
