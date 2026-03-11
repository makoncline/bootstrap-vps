# Bootstrap Prompt

Use this when you need to manually walk a fresh Hetzner Ubuntu 24.04 server through the same process as the scripts:

1. Confirm the server is reachable as `root` with `/Users/makon/.ssh/makon_admin_ed25519`.
2. Copy the `.env.production` host file and the `bootstrap/remote` plus `bootstrap/lib` directories to `/root/bootstrap-bundle` on the server.
3. Run `/root/bootstrap-bundle/remote/init-host.sh /root/bootstrap-bundle/host.env`.
4. If the script exits with code `42`, reboot the host, reconnect as `root`, and run the same `init-host.sh` command again.
5. From this Mac, verify `ssh makon@<TAILSCALE_HOSTNAME>` works over Tailscale.
6. Run the Cloudflare helper or otherwise sync the tunnel ingress config and create proxied `CNAME` records to `<TUNNEL_ID>.cfargotunnel.com` for every hostname in `TUNNEL_HOSTNAMES`.
7. Run `/root/bootstrap-bundle/remote/lockdown-ssh.sh /root/bootstrap-bundle/host.env`.
8. Over Tailscale SSH, run `codex login --device-auth` on the server and complete the first interactive sign-in flow.
9. Confirm `~/.codex/skills/server-new-app/SKILL.md` exists for the server operator user.
10. Confirm `unattended-upgrades` is installed, `/etc/docker/daemon.json` has log rotation, and `systemctl status bootstrap-healthcheck.timer` is healthy.
11. If Telegram credentials were provided, run `notify "[<HOSTNAME>] telegram test"` as the admin user and confirm the message arrives.
12. If the server needs GitHub access, generate a dedicated SSH key, add it to GitHub, and verify `ssh -T git@github.com`.
13. Confirm public SSH to the server IP fails, `docker compose version` works on the server, `tailscale status` is healthy, `codex --version` works, and `https://<SMOKE_HOSTNAME>` serves the sample `whoami` app through the tunnel.
