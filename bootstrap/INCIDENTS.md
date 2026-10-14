# Daylily incident evidence

`init-host.sh` installs the standard-library Python command as
`/usr/local/bin/daylily-incident`. For an existing host, install just this command:

```bash
sudo install -m 755 bootstrap/remote/daylily-incident.py /usr/local/bin/daylily-incident
```

Run with authorized journal, proxy-log and Docker access. Supply an explicit UTC
interval of at most 14 days and an app User ID, Clerk user ID, or correlation ID:

```bash
sudo daylily-incident \
  --start 2026-10-14T18:00:00Z --end 2026-10-14T19:00:00Z \
  --app-user-id VERIFIED_APP_USER_ID \
  --output /tmp/incident-evidence.json
```

The output must be a new file; existing files and symlinks are refused. JSON is
mode 600 and, when invoked through sudo, owned by the invoking user. Keep incident
outputs outside this public repository.

App/Clerk identities are verified with parameterized SELECTs against the deployed
app database; when both are supplied they must resolve to the same user. A
`--correlation-id` can be repeated and is reported as observed or unobserved in the
requested interval. Selection is the union of supplied identity and correlations.
The command only reads sources; it does not restart services, notify anyone, or
modify application data. It writes only the requested evidence file.

The report includes matching app, audit and proxy metadata, procedure outcomes,
durations and the current release/container-start snapshot. Existing audit
`clerkUserId` and request/error `clerk_user_id` fields are supported, along with
`clientRequestId` and `create_request_id`. Headers, cookies, credentials, email,
IP/User-Agent values, query strings, arbitrary payloads, stack traces and unknown
error messages are omitted. Only allowlisted routes and scalar fields are emitted.

`--row-counts` explicitly opts into SELECT-only listing/list aggregate counts for
the verified user. These are current snapshots, not historical counts.
`--infer-client` additionally includes requests matching the IP/User-Agent
fingerprint of correlated requests, clearly labeled as inference. Shared clients
can cause false attribution; inferred audit outcomes are excluded from aggregate
procedure counts, and correlations attributed to other actors are excluded.

Source coverage includes actual oldest/newest retained timestamps, read errors,
and malformed records. Coverage is partial if either interval edge lies outside
that span or records cannot be read. A spanning retention window cannot prove
there are no internal gaps. Unstructured app lines are counted and omitted. Source coverage does not establish when each
event type became instrumented.
HTTP 200 can accompany tRPC procedure failures; inspect app/audit outcomes as well
as proxy status. PostHog session IDs are absent from the current server schema.

## Proxy retention

The template mounts `./logs` and writes `access-retained.log` with 20 MiB rotations,
64 archives and the existing 168-hour age ceiling. Compression remains enabled.
At the measured peak complete rolling-hour rate of 9.687 MiB/hour, 1,280 MiB of
archives estimates 132 hours of coverage. The 96-hour target remains viable up to
13.33 MiB/hour. Re-measure if traffic grows; this capacity estimate is not a
promise of a historical window or recovered deleted records.

Budget approximately 1.27 GiB before compression, plus up to 20 MiB for the
legacy active writer while existing connections drain and temporary migration
headroom, and preserve at least 5 GiB free on this host. The active file is not
pruned by the archive age limit; archive expiry happens during Caddy maintenance.

For this already-running server, apply only the filename/count change to the
existing Caddyfile, preserving routes and header filtering. Validate the proposed
file inside the Caddy container with `caddy validate --config - --adapter caddyfile`
before changing the bind-mounted file. Write that file in place so the running
container keeps the correct bind-mount inode. Save the original outside the log
directory before editing.

Caddy started with `caddy run --config /etc/caddy/Caddyfile --adapter caddyfile`
can gracefully reload the original file via `docker kill --signal=USR1 proxy-caddy-1`
with the admin API disabled. Do not use this route if Caddy was started with
`--resume` or its config was changed through the API. Changing rotation options
for the same output filename requires a restart; the new filename creates a new
writer on reload. See [Caddy signals](https://caddyserver.com/docs/command-line#signals)
and [file log options](https://caddyserver.com/docs/caddyfile/directives/log#file).

Verify the new writer, unchanged container start/PID and application start/release,
and public health checks. If validation or reload fails, restore the original file
in place and reload it; do not restart applications.

Preserve old history: rename closed `access-*-size.log.gz` archives to
`access-retained-*-size.log.gz`, preserving embedded rotation timestamps. A graceful
reload can leave the old active writer open while existing connections drain;
keep `access.log` intact in that case. Once its descriptor is closed, compress it under that same
rotation naming scheme, verify its uncompressed checksum, then replace `access.log`
with a compatibility symlink to `access-retained.log`. Keep mode 600. This places
old archives under the new writer's count/age policy. Do not compress an open
writer or discard historical content. The exporter reads both prefixes and
avoids counting the symlink twice. Collectors using `find -type f` should explicitly
read the new active filename.

After applying, report the actual oldest retained record separately from the
capacity estimate: increasing the limit cannot instantly create 96 hours of logs.
