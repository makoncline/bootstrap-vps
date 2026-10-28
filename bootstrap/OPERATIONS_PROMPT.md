# Host operations

Read `bootstrap/README.md` before setup or reconciliation. Use the host's private env file and configured admin account. Keep credentials and host-specific configuration out of commits and chat output.

Use `bootstrap/local/bootstrap-host.sh` for a fresh host. Use `bootstrap/local/reconcile-host.sh` only after completing the documented OpenSSH transition and arranging recovery access. Both commands can upgrade packages and reboot the host.

After setup, run `sudo bootstrapctl status` through the intended non-interactive SSH connection. Confirm the applied revision, installed tools, service state, and setup result. Use `sudo bootstrapctl diagnose` for a bounded failure report, then target the relevant logs.

Apply routes through `bootstrap-apply-proxy`; wait for its result before reporting success. Keep repairs, cleanup, reboots, and deployment separate from a read-only audit.
