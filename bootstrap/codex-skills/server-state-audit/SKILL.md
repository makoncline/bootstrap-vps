---
name: server-state-audit
description: Inspect VPS health, resource pressure, deployment results, and logs on hosts set up by this repository.
---

# Server State Audit

On a host the user authorized you to inspect:

1. Run `sudo bootstrapctl status`. Read the applied revision, setup result, current health, resource data, and service states.
2. Follow a specific finding with `sudo bootstrapctl logs <component>`. Components are `host`, `actions`, `health`, `access`, or a stack name such as `proxy` or `tunnel`.
3. Use `sudo bootstrapctl diagnose` when the cause spans components. It gathers bounded recent evidence and marks unavailable, timed-out, or truncated sections. Missing evidence does not prove that a service failed.
4. Report the observed problem, supporting action ID or timestamp, and the smallest next step. State any visibility limit. Keep raw logs out of the final reply unless a short excerpt is needed.

These commands read state; they make no repairs. Treat setup marked `reboot_required` as an incomplete setup awaiting the expected reboot. An interrupted run needs investigation before another bootstrap attempt.

Use the current admin and sudo access. The command is a diagnostic interface, not an access-control boundary. Review output before sharing it; application logs can contain values that automatic redaction cannot identify. Keep environment files, complete process arguments, credentials, and subscription sessions out of reports.

When an authorized task requires stopping a process, use its exact service or Compose project, or the PID captured when the task started it. Keep test state in disposable directories and containers. Cleanup and host changes require a separate instruction from the user.
