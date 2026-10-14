#!/usr/bin/env python3
"""Bounded, read-only Daylily evidence export. Requires authorized journal/Docker access."""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

UTC = dt.timezone.utc
CONTAINER = 'daylilycatalog-app-1'
HOSTS = {'daylilycatalog.com', 'www.daylilycatalog.com', 'prod.daylilycatalog.com'}
FIELDS = {'timestamp', 'event', 'release', 'correlation_id', 'cf_ray', 'method',
          'procedure', 'operation_type', 'duration_ms', 'durationMs', 'http_status',
          'error_code', 'status', 'appUserId', 'clerkUserId', 'clerk_user_id',
          'clientRequestId', 'create_request_id',
          'id', 'listingId', 'listId', 'imageId', 'referenceId', 'cultivarReferenceId',
          'outcome', 'decision', 'reason', 'action', 'level'}
SAFE_MESSAGES = {'fetch failed', 'other side closed', 'Listing not found',
                 'Not authenticated', 'An unexpected error occurred',
                 'Request ID was already used for different data.'}


def instant(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValueError('Use an explicit UTC timestamp (Z or +00:00).')
    return parsed.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def clean(value):
    """Whitelist scalar fields; never export arbitrary input, headers or exception text."""
    if isinstance(value, float) and not math.isfinite(value):
        return '[omitted]'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str) or len(value) > 256:
        return '[omitted]'
    if '@' in value or '://' in value or re.search(r'(?i)bearer\s|basic\s', value):
        return '[redacted]'
    if re.search(r'\b(?:sk_|pk_|whsec_|eyJ)[A-Za-z0-9_.-]+', value):
        return '[redacted]'
    try:
        ipaddress.ip_address(value)
        return '[redacted]'
    except ValueError:
        pass
    if re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', value):
        return '[redacted]'
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', value):
        return '[omitted]'
    return value


def endpoint(value):
    path = str(value).split('?', 1)[0].split('#', 1)[0]
    # Only application routes are useful; scan payloads and unknown slugs stay private.
    if path.startswith('/api/trpc/'):
        names = path[len('/api/trpc/'):]
        if re.fullmatch(r'[A-Za-z0-9_.,]+', names):
            return path
    if re.fullmatch(r'/(?:api/(?:clerk-webhook|runtime-config|v1/(?:status|search|match))|dashboard(?:/(?:listings|lists|settings|add-listing))?|catalogs|mcp)', path):
        return path
    return '[other route]'


def error_excerpt(error, depth=0):
    if not isinstance(error, dict) or depth > 3:
        return None
    result = {k: clean(error[k]) for k in ('name', 'code') if k in error}
    if isinstance(error.get('message'), str):
        result['message'] = error['message'] if error['message'] in SAFE_MESSAGES else '[message omitted]'
    if isinstance(error.get('cause'), dict):
        result['cause'] = error_excerpt(error['cause'], depth + 1)
    return result


def app_excerpt(record, at):
    result = {k: clean(v) for k, v in record.items() if k in FIELDS}
    result['journal_timestamp'] = iso(at)
    if 'pathname' in record:
        result['endpoint'] = endpoint(record['pathname'])
    if record.get('error'):
        result['error'] = error_excerpt(record['error'])
    return result


def actor_matches(record, identity):
    if not identity or other_actor(record, identity):
        return False
    return (record.get('appUserId') == identity['appUserId'] or
            bool(identity.get('clerkUserId')) and any(
                record.get(field) == identity['clerkUserId']
                for field in ('clerkUserId', 'clerk_user_id')) or
            record.get('event') == 'image_moderation' and record.get('userId') == identity['appUserId'])


def other_actor(record, identity):
    if not identity:
        return False
    return (record.get('appUserId') not in (None, identity['appUserId']) or
            any(record.get(field) not in (None, identity.get('clerkUserId'))
                for field in ('clerkUserId', 'clerk_user_id')) or
            record.get('event') == 'image_moderation' and
            record.get('userId') not in (None, identity['appUserId']))


def header(headers, name):
    value = next((v for k, v in headers.items() if k.lower() == name.lower()), '')
    return ','.join(value) if isinstance(value, list) else str(value)


def fingerprint(request):
    headers = request.get('headers', {})
    address = header(headers, 'Cf-Connecting-Ip') or header(headers, 'X-Forwarded-For') or request.get('client_ip', '')
    agent = header(headers, 'User-Agent')
    return hashlib.sha256((address + '\0' + agent).encode()).digest() if address and agent else None


def records_from_lines(lines, source):
    for line in lines:
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError('Expected an object')
            if source == 'app':
                at = dt.datetime.fromtimestamp(int(raw['__REALTIME_TIMESTAMP']) / 1e6, UTC)
                message = raw.get('MESSAGE', '')
                if not isinstance(message, str):
                    raise ValueError('Expected a message string')
                beginning = message.find('{')
                record = json.loads(message[beginning:]) if beginning >= 0 else {}
                if not isinstance(record, dict):
                    raise ValueError('Expected a log object')
                if any(record.get(field) is not None and not isinstance(record[field], str)
                       for field in ('correlation_id', 'cf_ray')):
                    raise ValueError('Expected string correlation identifiers')
            else:
                at = dt.datetime.fromtimestamp(float(raw['ts']), UTC)
                record = raw
                request = record.get('request')
                if not isinstance(request, dict) or not isinstance(request.get('headers', {}), dict):
                    raise ValueError('Expected a request object')
                if any(not isinstance(request.get(field), str) for field in ('host', 'method', 'uri')):
                    raise ValueError('Expected string request fields')
                if not isinstance(request.get('client_ip', ''), str):
                    raise ValueError('Expected a client address string')
                if any(not (isinstance(value, str) or isinstance(value, list) and
                            all(isinstance(item, str) for item in value))
                       for value in request.get('headers', {}).values()):
                    raise ValueError('Expected string header values')
                if not isinstance(record.get('status'), int) or not isinstance(record.get('duration'), (int, float)) or not math.isfinite(record['duration'] * 1000):
                    raise ValueError('Expected numeric response fields')
            yield at, record
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            yield None, None


def command_lines(args):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        yield from process.stdout
        if process.wait() != 0:
            raise OSError('Source command failed')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()


def journal(start=None, end=None, limit=None):
    args = ['journalctl', '-t', CONTAINER, '--no-pager', '-o', 'json']
    if start:
        args += ['--since', iso(start), '--until', iso(end)]
    if limit:
        args += ['-n', str(limit)]
    yield from records_from_lines(command_lines(args), 'app')


def journal_configuration():
    template = '{"driver":{{json .HostConfig.LogConfig.Type}},"tag":{{json (index .HostConfig.LogConfig.Config "tag")}}}'
    try:
        result = subprocess.run(['docker', 'inspect', CONTAINER, '--format', template],
                                text=True, capture_output=True, timeout=15)
        if result.returncode:
            return 'unavailable'
        config = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'unavailable'
    return 'ready' if config.get('driver') == 'journald' and config.get('tag') == '{{.Name}}' else 'configuration-required'


def proxy_files(directory):
    seen = set()
    for path in sorted(Path(directory).glob('access*.log*')):
        stat = path.stat()
        inode = (stat.st_dev, stat.st_ino)
        if inode not in seen and path.is_file():
            seen.add(inode)  # Compatibility symlink must not double-count the active log.
            yield path


def proxy(directory):
    for path in proxy_files(directory):
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt') as stream:
            yield from records_from_lines(stream, 'proxy')


def coverage(oldest, newest, start, end, errors=0, malformed=0):
    status = 'complete' if oldest and oldest <= start and newest and newest >= end and not errors and not malformed else 'partial'
    if not oldest:
        status = 'unavailable'
    return {'status': status, 'oldest_retained': iso(oldest) if oldest else None,
            'newest_retained': iso(newest) if newest else None,
            'start_precedes_retained_records': bool(oldest and start < oldest),
            'end_follows_retained_records': bool(newest and end > newest),
            'read_errors': errors, 'malformed_records': malformed,
            'basis': 'Retention span and successful reads; internal gaps cannot be proven absent.'}


def select_proxy(rows, confirmed, blocked, rays, infer_client, fingerprints=None):
    def direct(record):
        headers = record.get('request', {}).get('headers', {})
        return header(headers, 'X-Correlation-Id') in confirmed or header(headers, 'Cf-Ray') in rays
    if fingerprints is None:
        fingerprints = {fingerprint(r.get('request', {})) for _, r in rows
                        if r.get('request', {}).get('host') in HOSTS and direct(r) and
                        header(r.get('request', {}).get('headers', {}), 'X-Correlation-Id') not in blocked} - {None}
    selected = []
    inferred_cids = set()
    inferred_rays = set()
    for at, record in rows:
        request = record.get('request', {})
        if request.get('host') not in HOSTS:
            continue
        headers = request.get('headers', {})
        cid, ray = header(headers, 'X-Correlation-Id'), header(headers, 'Cf-Ray')
        if cid in blocked:
            continue
        match = direct(record)
        inferred = infer_client and fingerprint(request) in fingerprints
        if not match and not inferred:
            continue
        basis = 'correlation' if match else 'client-inferred; identity not proven'
        if not match:
            if cid: inferred_cids.add(cid)
            if ray: inferred_rays.add(ray)
        selected.append({'timestamp': iso(at), 'method': clean(request.get('method')),
                         'endpoint': endpoint(request.get('uri', '')),
                         'http_status': record.get('status'),
                         'duration_ms': round(record.get('duration', 0) * 1000, 3),
                         'correlation_id': clean(cid), 'cf_ray': clean(ray), 'attribution': basis})
    return selected, inferred_cids, inferred_rays


NODE_QUERY = r'''
import { createClient } from '/app/node_modules/@libsql/client/lib-esm/node.js';
import { readFileSync } from 'node:fs';
const input = JSON.parse(readFileSync(0, 'utf8'));
const db = createClient({url:process.env.DATABASE_URL,authToken:process.env.TURSO_DATABASE_AUTH_TOKEN});
try {
 const rows = (await db.execute({sql:'SELECT id,clerkUserId FROM User WHERE id=? OR clerkUserId=?',args:[input.appUserId??'',input.clerkUserId??'']})).rows;
 if(rows.length!==1 || input.appUserId && rows[0].id!==input.appUserId || input.clerkUserId && rows[0].clerkUserId!==input.clerkUserId) throw new Error('Identity mismatch');
 const identity={appUserId:rows[0].id,clerkUserId:rows[0].clerkUserId};
 const result={identity};
 if(input.rowCounts){
  result.rowCounts={};
  for(const [name,sql] of [['listings','SELECT COUNT(*) AS total,SUM(CASE WHEN status IS NULL OR status<>\'HIDDEN\' THEN 1 ELSE 0 END) AS nonHidden FROM Listing WHERE userId=?'],['lists','SELECT COUNT(*) AS total FROM List WHERE userId=?']]) result.rowCounts[name]=(await db.execute({sql,args:[identity.appUserId]})).rows[0];
  result.rowCounts.snapshotUTC=new Date().toISOString();
 }
 console.log(JSON.stringify(result));
} catch {console.error('Identity or SELECT-only count verification failed');process.exitCode=1;} finally {db.close();}
'''


def verify_identity(args):
    if not args.app_user_id and not args.clerk_user_id:
        return {}, None
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'node', '--input-type=module', '-e', NODE_QUERY],
                            input=json.dumps({'appUserId': args.app_user_id, 'clerkUserId': args.clerk_user_id,
                                              'rowCounts': args.row_counts}), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('Could not verify the supplied User/Clerk identity; no evidence exported.')
    data = json.loads(result.stdout)
    return data['identity'], data.get('rowCounts')


def deployment():
    template = '{"release":{{json (index .Config.Labels "org.opencontainers.image.revision")}},"image":{{json .Config.Image}},"startedUTC":{{json .State.StartedAt}},"state":{{json .State.Status}},"restartCount":{{.RestartCount}},"oomKilled":{{.State.OOMKilled}}}'
    try:
        result = subprocess.run(['docker', 'inspect', CONTAINER, '--format', template], text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError()
        return {'status': 'available', 'capturedUTC': iso(dt.datetime.now(UTC)), **json.loads(result.stdout)}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'status': 'unavailable'}


def export(args, identity, row_counts):
    confirmed, blocked, rays = set(args.correlation_id), set(), set()
    app_errors = malformed = legacy = 0
    oldest = newest = None
    app_configuration = journal_configuration()
    try:
        if app_configuration != 'ready':
            raise OSError('Required journald configuration is unavailable')
        stream = journal()
        try:
            oldest, _ = next(stream, (None, None))
        finally:
            stream.close()
        for at, _ in journal(limit=1):
            newest = at
        # First pass retains identifiers only; no raw window or private payload is buffered.
        for at, record in journal(args.start, args.end):
            if at is None:
                malformed += 1
            elif not record:
                legacy += 1
            else:
                cid = record.get('correlation_id')
                if actor_matches(record, identity):
                    if cid: confirmed.add(cid)
                    if record.get('cf_ray'): rays.add(record['cf_ray'])
                elif cid and other_actor(record, identity):
                    blocked.add(cid)
                if cid in args.correlation_id and record.get('cf_ray'):
                    rays.add(record['cf_ray'])
    except OSError:
        app_errors += 1
    blocked -= set(args.correlation_id)
    proxy_oldest = proxy_newest = None
    proxy_errors = proxy_malformed = 0
    fingerprints = set()
    try:
        for at, record in proxy('/srv/stacks/proxy/logs'):
            if at is None:
                proxy_malformed += 1
                continue
            proxy_oldest = min(proxy_oldest or at, at)
            proxy_newest = max(proxy_newest or at, at)
            if not args.start <= at <= args.end:
                continue
            request = record.get('request', {})
            headers = request.get('headers', {})
            cid, ray = header(headers, 'X-Correlation-Id'), header(headers, 'Cf-Ray')
            if request.get('host') in HOSTS and cid not in blocked and (cid in confirmed or ray in rays):
                value = fingerprint(request)
                if value: fingerprints.add(value)
    except (OSError, EOFError):
        proxy_errors += 1

    def bounded_proxy():
        for at, record in proxy('/srv/stacks/proxy/logs'):
            if at and args.start <= at <= args.end:
                yield at, record

    proxy_matches, inferred_cids, inferred_rays = [], set(), set()
    try:
        proxy_matches, inferred_cids, inferred_rays = select_proxy(
            bounded_proxy(), confirmed, blocked, rays, args.infer_client, fingerprints)
    except (OSError, EOFError):
        proxy_errors += 1
    matches = []
    try:
        for at, record in journal(args.start, args.end) if app_configuration == 'ready' else ():
            if not record:
                continue
            cid = record.get('correlation_id')
            if cid in blocked:
                continue
            actor = actor_matches(record, identity)
            exact = cid in confirmed or record.get('cf_ray') in rays
            inferred = cid in inferred_cids or record.get('cf_ray') in inferred_rays
            if actor or exact or inferred:
                item = app_excerpt(record, at)
                item['attribution'] = 'verified-actor' if actor else 'correlation' if exact else 'client-inferred; identity not proven'
                matches.append(item)
    except OSError:
        app_errors += 1
    outcomes = collections.Counter((r.get('procedure'), r.get('status')) for r in matches
                                   if r.get('event') == 'user_mutation' and r['attribution'] != 'client-inferred; identity not proven')
    return {'schemaVersion': 1, 'requestedUTC': {'start': iso(args.start), 'end': iso(args.end)},
            'capturedUTC': iso(dt.datetime.now(UTC)), 'verifiedIdentity': identity or None,
            'requestedCorrelationIds': args.correlation_id,
            'correlationVerification': {cid: any(r.get('correlation_id') == cid for r in matches + proxy_matches)
                                        for cid in args.correlation_id},
            'deploymentNow': deployment(), 'rowCountsNow': row_counts,
            'sources': {'appAndAudit': {**coverage(oldest, newest, args.start, args.end, app_errors, malformed), 'unstructuredRecordsOmitted': legacy,
                                                'configuration': app_configuration,
                                                'requirement': 'journald driver with tag {{.Name}}; retained journal covers container replacements'},
                        'proxyAccess': coverage(proxy_oldest, proxy_newest, args.start, args.end, proxy_errors, proxy_malformed)},
            'procedureOutcomes': [{'procedure': key[0], 'status': key[1], 'count': count} for key, count in sorted(outcomes.items(), key=lambda item: str(item[0]))],
            'appAndAuditMatches': matches, 'proxyMatches': proxy_matches,
            'limits': ['PostHog session IDs are not present in the current server schema.',
                       'Only allowlisted fields and safe system error messages are exported.',
                       'Fingerprint matches are inference; they do not prove caller identity.',
                       'Current deployment/count snapshots do not describe historical state.',
                       'Source coverage does not establish when individual event types became instrumented.']}


def write_private(path, evidence):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        if os.geteuid() == 0 and os.environ.get('SUDO_UID', '').isdigit():
            os.fchown(output.fileno(), int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
        json.dump(evidence, output, indent=2, allow_nan=False)
        output.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, type=instant)
    parser.add_argument('--end', required=True, type=instant)
    parser.add_argument('--app-user-id')
    parser.add_argument('--clerk-user-id')
    parser.add_argument('--correlation-id', action='append', default=[])
    parser.add_argument('--infer-client', action='store_true', help='Include labeled IP/User-Agent inference; never identity proof.')
    parser.add_argument('--row-counts', action='store_true', help='Explicit SELECT-only current listing/list counts.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not dt.timedelta(0) < args.end - args.start <= dt.timedelta(days=14):
        parser.error('Interval must be positive and no longer than 14 days.')
    if args.end > dt.datetime.now(UTC):
        parser.error('End must not be in the future.')
    if not (args.app_user_id or args.clerk_user_id or args.correlation_id):
        parser.error('Supply an app User ID, Clerk ID or correlation ID.')
    if args.row_counts and not (args.app_user_id or args.clerk_user_id):
        parser.error('Row counts require a verified user identity.')
    for identifier in [args.app_user_id, args.clerk_user_id, *args.correlation_id]:
        if identifier and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
            parser.error('IDs must contain only letters, digits, underscores or hyphens.')
    try:
        identity, counts = verify_identity(args)
        evidence = export(args, identity, counts)
        write_private(args.output, evidence)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        parser.exit(1, 'Could not verify identity or create a new private output file; no existing file overwritten.\n')
    partial = any(source['status'] != 'complete' for source in evidence['sources'].values())
    print(json.dumps({'output': str(args.output), 'partialSources': partial,
                      'appMatches': len(evidence['appAndAuditMatches']), 'proxyMatches': len(evidence['proxyMatches'])}))


if __name__ == '__main__':
    main()
