#!/usr/bin/env python3
"""Synthetic tests: no Docker, journal, network, or real identities."""
import argparse
import copy
import datetime as dt
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'bootstrap/remote/daylily-incident.py'
spec = importlib.util.spec_from_file_location('incident', SCRIPT)
i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i)
START = i.instant('2026-01-01T01:00:00Z')
END = START + dt.timedelta(hours=1)
IDENTITY = {'appUserId': 'test_app_user', 'clerkUserId': 'user_test_actor'}


def request(cid, ray='ray_test', host='daylilycatalog.com'):
    return {'ts': START.timestamp(), 'status': 200, 'duration': 0.25,
            'request': {'host': host, 'method': 'POST',
                        'uri': '/api/trpc/dashboardDb.listing.create?input=secret-test-input',
                        'headers': {'X-Correlation-Id': [cid], 'Cf-Ray': [ray],
                                    'Cf-Connecting-Ip': ['192.0.2.1'], 'User-Agent': ['test-agent'],
                                    'Authorization': ['Bearer synthetic-token'], 'Cookie': ['private=test']}}}


class EvidenceTests(unittest.TestCase):
    def test_redaction_and_nested_errors(self):
        for value in ('someone@example.invalid', 'Bearer synthetic-token', '192.0.2.1',
                      '2001:db8::1', 'https://example.invalid/private', 'sk_test_synthetic',
                      'pk_test_synthetic', 'whsec_synthetic', 'eyJsynthetic.token', 'Jane Example'):
            self.assertIn(i.clean(value), ('[redacted]', '[omitted]'))
        record = {'event': 'application_error', 'correlation_id': 'test_correlation',
                  'clerk_user_id': 'user_test_actor', 'create_request_id': 'test_nonce',
                  'duration_ms': 500, 'input': {'secret': 'not-exported'}, 'email': 'hidden@example.invalid',
                  'pathname': '/api/trpc/dashboardDb.listing.create?input=private',
                  'error': {'name': 'TypeError', 'message': 'private member details',
                            'stack': 'private stack', 'cause': {'code': 'UND_ERR_SOCKET', 'message': 'fetch failed'}}}
        result = i.app_excerpt(record, START)
        text = json.dumps(result)
        for private in ('not-exported', 'hidden@example.invalid', '?input', 'private member details', 'private stack'):
            self.assertNotIn(private, text)
        self.assertEqual(result['error']['cause']['code'], 'UND_ERR_SOCKET')
        self.assertEqual(result['clerk_user_id'], 'user_test_actor')
        self.assertEqual(result['create_request_id'], 'test_nonce')
        self.assertEqual(i.endpoint('/catalog/private-garden'), '[other route]')
        self.assertEqual(i.error_excerpt({'message': ['malformed']}), {})
        self.assertEqual(i.clean(float('inf')), '[omitted]')
        self.assertEqual(i.clean(float('nan')), '[omitted]')

    def test_verified_actor_schema(self):
        for record in ({'appUserId': 'test_app_user'}, {'clerkUserId': 'user_test_actor'},
                       {'clerk_user_id': 'user_test_actor'}, {'event': 'image_moderation', 'userId': 'test_app_user'}):
            self.assertTrue(i.actor_matches(record, IDENTITY))
            self.assertFalse(i.other_actor(record, IDENTITY))
        self.assertFalse(i.actor_matches({}, {'appUserId': 'test_app_user', 'clerkUserId': None}))
        self.assertTrue(i.other_actor({'clerk_user_id': 'user_other'}, IDENTITY))
        self.assertFalse(i.actor_matches({'appUserId': 'test_app_user', 'clerk_user_id': 'user_other'}, IDENTITY))

    def test_proxy_exact_and_optional_inference(self):
        rows = [(START, request('exact')), (START, request('inferred', 'other_ray')),
                (START, request('known_other', 'other_ray')),
                (START, request('exact', host='unrelated.example.invalid'))]
        selected, cids, rays = i.select_proxy(rows, {'exact'}, {'known_other'}, set(), False)
        self.assertEqual(len(selected), 1)
        self.assertEqual(cids, set())
        self.assertEqual(selected[0]['duration_ms'], 250)
        selected, cids, rays = i.select_proxy(rows, {'exact'}, {'known_other'}, set(), True)
        self.assertEqual(len(selected), 2)
        self.assertEqual(cids, {'inferred'})
        self.assertIn('identity not proven', selected[1]['attribution'])
        for private in ('192.0.2.1', 'test-agent', 'synthetic-token', 'private=test', 'secret-test-input'):
            self.assertNotIn(private, json.dumps(selected))

    def test_coverage_edges_and_errors(self):
        self.assertEqual(i.coverage(START, END, START, END)['status'], 'complete')
        self.assertEqual(i.coverage(START, END, START-dt.timedelta(seconds=1), END)['status'], 'partial')
        self.assertEqual(i.coverage(START, END, START, END+dt.timedelta(seconds=1))['status'], 'partial')
        self.assertEqual(i.coverage(START, END, START, END, malformed=1)['status'], 'partial')
        self.assertEqual(i.coverage(START, END, START, END, errors=1)['status'], 'partial')
        self.assertEqual(i.coverage(None, None, START, END)['status'], 'unavailable')

    def test_plain_gzip_symlink_and_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'access-retained.log').write_text(json.dumps(request('one'))+'\ninvalid\n')
            (root/'access.log').symlink_to('access-retained.log')
            with gzip.open(root/'access-retained-test-size.log.gz', 'wt') as stream:
                stream.write(json.dumps(request('two'))+'\n')
            rows = list(i.proxy(root))
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(at is None for at, _ in rows), 1)
            self.assertEqual(list(i.records_from_lines(['[]', '{"ts":"bad"}'], 'proxy')), [(None,None), (None,None)])

    def test_corrupted_audit_is_partial_and_startup_is_legacy(self):
        def line(message, at=START):
            return json.dumps({'__REALTIME_TIMESTAMP': str(int(at.timestamp()*1e6)), 'MESSAGE': message})
        window = [line('Server ready'), line('INFO {"event":"user_mutation","appUserId":"test_app_user",'),
                  line('{"event":"user_mutation","correlation_id":[]}')]
        self.assertEqual(list(i.records_from_lines(window, 'app')), [(START, {}), (None, None), (None, None)])
        def journal(start=None, end=None, limit=None):
            lines = window if start else [line('Server ready', END if limit else START)]
            yield from i.records_from_lines(lines, 'app')
        args = argparse.Namespace(start=START, end=END, correlation_id=[], infer_client=False)
        with patch.object(i, 'journal_configuration', lambda: 'ready'), patch.object(i, 'journal', journal), patch.object(i, 'proxy', lambda _: iter([])), \
             patch.object(i, 'deployment', lambda: {'status': 'unavailable'}):
            result = i.export(args, IDENTITY, None)
        source = result['sources']['appAndAudit']
        self.assertEqual(source['status'], 'partial')
        self.assertEqual(source['malformed_records'], 2)
        self.assertEqual(source['unstructuredRecordsOmitted'], 1)
        self.assertEqual(result['appAndAuditMatches'], [])

    def test_non_journald_backend_reports_configuration_required(self):
        response = subprocess.CompletedProcess([], 0, '{"driver":"json-file","tag":null}', '')
        args = argparse.Namespace(start=START, end=END, correlation_id=['synthetic'], infer_client=False)
        with patch.object(i.subprocess, 'run', return_value=response), \
             patch.object(i, 'journal', side_effect=AssertionError('Must not read an unsupported journal source')), \
             patch.object(i, 'proxy', lambda _: iter([])), \
             patch.object(i, 'deployment', lambda: {'status': 'unavailable'}):
            result = i.export(args, {}, None)
        self.assertEqual(result['sources']['appAndAudit']['status'], 'unavailable')
        self.assertEqual(result['sources']['appAndAudit']['configuration'], 'configuration-required')
        self.assertEqual(result['appAndAuditMatches'], [])

    def test_corrupt_proxy_fields_produce_partial_evidence(self):
        first = request('synthetic')
        last = request('synthetic')
        last['ts'] = END.timestamp()
        args = argparse.Namespace(start=START, end=END, correlation_id=['synthetic'], infer_client=True)
        for field, value in [('host', []), ('method', {}), ('uri', []),
                             ('client_ip', 123), ('client_ip', []),
                             ('headers', {'User-Agent': [123]}),
                             ('headers', {'Cf-Ray': [None]}),
                             ('headers', {'X-Correlation-Id': [{}]}),
                             ('duration', 1e308)]:
            with self.subTest(field=field):
                broken = copy.deepcopy(first)
                if field == 'duration':
                    broken[field] = value
                else:
                    broken['request'][field] = value
                lines = [json.dumps(record) for record in (first, broken, last)]
                with patch.object(i, 'journal_configuration', lambda: 'ready'), \
                     patch.object(i, 'journal', lambda *a, **kw: (row for row in ())), \
                     patch.object(i, 'proxy', lambda _: i.records_from_lines(lines, 'proxy')), \
                     patch.object(i, 'deployment', lambda: {'status': 'unavailable'}):
                    result = i.export(args, {}, None)
                self.assertEqual(result['sources']['proxyAccess']['status'], 'partial')
                self.assertEqual(result['sources']['proxyAccess']['malformed_records'], 1)
                self.assertEqual(len(result['proxyMatches']), 2)
                json.dumps(result, allow_nan=False)

    def test_invalid_utf8_in_plain_and_gzip_logs_is_partial(self):
        first = request('synthetic')
        last = request('synthetic')
        last['ts'] = END.timestamp()
        payload = json.dumps(first).encode() + b'\n\xff\n' + json.dumps(last).encode() + b'\n'
        original_proxy = i.proxy
        args = argparse.Namespace(start=START, end=END, correlation_id=['synthetic'], infer_client=False)
        for compressed in (False, True):
            with self.subTest(compressed=compressed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ('access-test-size.log.gz' if compressed else 'access.log')
                if compressed:
                    with gzip.open(path, 'wb') as stream:
                        stream.write(payload)
                else:
                    path.write_bytes(payload)
                with patch.object(i, 'journal_configuration', lambda: 'ready'), \
                     patch.object(i, 'journal', lambda *a, **kw: (row for row in ())), \
                     patch.object(i, 'proxy', lambda _: original_proxy(directory)), \
                     patch.object(i, 'deployment', lambda: {'status': 'unavailable'}):
                    result = i.export(args, {}, None)
                self.assertEqual(result['sources']['proxyAccess']['status'], 'partial')
                self.assertEqual(result['sources']['proxyAccess']['malformed_records'], 1)
                self.assertEqual(len(result['proxyMatches']), 2)
                output = Path(directory) / 'evidence.json'
                i.write_private(output, result)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(len(json.loads(output.read_text())['proxyMatches']), 2)

    def test_gzip_read_errors_preserve_matches_before_and_after(self):
        first = request('synthetic', 'ray_before')
        last = request('synthetic', 'ray_after')
        last['ts'] = END.timestamp()
        encoded = gzip.compress(json.dumps(first).encode() + b'\n')
        corrupt = bytearray(encoded)
        corrupt[10] = 0xff  # Invalid deflate block type produces zlib.error.
        original_proxy = i.proxy
        args = argparse.Namespace(start=START, end=END, correlation_id=['synthetic'], infer_client=False)
        for damaged in (encoded[:-8], bytes(corrupt)):
            with self.subTest(damaged=damaged[:12]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'access-01.log').write_text(json.dumps(first) + '\n')
                (root / 'access-02-size.log.gz').write_bytes(damaged)
                (root / 'access-03.log').write_text(json.dumps(last) + '\n')
                with patch.object(i, 'journal_configuration', lambda: 'ready'), \
                     patch.object(i, 'journal', lambda *a, **kw: (row for row in ())), \
                     patch.object(i, 'proxy', lambda _: original_proxy(directory)), \
                     patch.object(i, 'deployment', lambda: {'status': 'unavailable'}):
                    result = i.export(args, {}, None)
                self.assertEqual(result['sources']['proxyAccess']['status'], 'partial')
                self.assertEqual(result['sources']['proxyAccess']['read_errors'], 1)
                self.assertTrue({'ray_before', 'ray_after'} <= {r['cf_ray'] for r in result['proxyMatches']})
                output = root / 'evidence.json'
                i.write_private(output, result)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(output.read_text())['sources']['proxyAccess']['read_errors'], 1)

    def test_private_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'evidence.json'
            i.write_private(out, {'safe': True})
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                i.write_private(out, {'replacement': True})
            linked = Path(directory)/'linked.json'
            linked.symlink_to(out)
            with self.assertRaises(FileExistsError):
                i.write_private(linked, {})
            self.assertEqual(json.loads(out.read_text()), {'safe': True})

    def test_full_export_excludes_other_actor_and_inferred_outcomes(self):
        args = argparse.Namespace(start=START, end=END, correlation_id=['missing'], infer_client=True)
        app = [(START, {'event': 'user_mutation', 'appUserId': 'test_app_user', 'correlation_id': 'exact',
                        'status': 'error', 'procedure': 'dashboardDb.listing.create'}),
               (START, {'event': 'application_error', 'correlation_id': 'exact',
                        'error': {'code': 'INTERNAL_SERVER_ERROR', 'message': 'fetch failed'}}),
               (START, {'event': 'user_mutation', 'correlation_id': 'inferred', 'status': 'success', 'procedure': 'test'}),
               (START, {'clerk_user_id': 'user_other', 'correlation_id': 'known_other'})]
        def journal(start=None, end=None, limit=None):
            yield from ([(START, {})] if start is None and not limit else [(END, {})] if limit else app)
        proxies = [(START,request('exact')), (START,request('inferred','ray_inferred')),
                   (START,request('known_other','ray_other')), (END,request('not_selected','ray_end'))]
        with patch.object(i, 'journal_configuration', lambda: 'ready'), patch.object(i, 'journal', journal), patch.object(i, 'proxy', lambda _: iter(proxies)), \
             patch.object(i, 'deployment', lambda: {'status': 'available', 'release': 'synthetic'}):
            result = i.export(args, IDENTITY, None)
        self.assertEqual(len(result['appAndAuditMatches']), 3)
        self.assertEqual(result['procedureOutcomes'], [{'procedure':'dashboardDb.listing.create','status':'error','count':1}])
        self.assertFalse(result['correlationVerification']['missing'])
        self.assertEqual(result['sources']['proxyAccess']['status'], 'complete')
        self.assertIsNone(result['rowCountsNow'])
        self.assertNotIn('user_other', json.dumps(result))

    def test_identity_mismatch_fails_closed_and_counts_opt_in(self):
        args = argparse.Namespace(app_user_id='test_app_user', clerk_user_id='user_test_actor', row_counts=False)
        with patch.object(i.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'secret')):
            with self.assertRaisesRegex(ValueError, 'Could not verify'):
                i.verify_identity(args)
        with patch.object(i.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({'identity':IDENTITY}), '')) as run:
            identity, counts = i.verify_identity(args)
        self.assertEqual(identity, IDENTITY)
        self.assertIsNone(counts)
        self.assertFalse(json.loads(run.call_args.kwargs['input'])['rowCounts'])
        self.assertNotIn('secret', str(run.call_args))

    def test_cli_rejects_unbounded_or_unsafe_input(self):
        base = ['--start', '2026-01-01T01:00:00Z', '--end', '2026-01-01T02:00:00Z',
                '--correlation-id', 'synthetic', '--output', '/tmp/unused-synthetic.json']
        invalid = [base+['--row-counts'], base+['--app-user-id','email@example.invalid'],
                   ['--start','2026-01-01T01:00:00','--end','2026-01-01T02:00:00Z']+base[4:],
                   ['--start','2026-01-01T01:00:00-07:00','--end','2026-01-01T02:00:00Z']+base[4:],
                   ['--start','2026-01-01T01:00:00Z','--end','2026-02-01T02:00:00Z']+base[4:],
                   ['--start','2026-01-01T02:00:00Z','--end','2026-01-01T01:00:00Z']+base[4:],
                   ['--start','2099-01-01T01:00:00Z','--end','2099-01-01T02:00:00Z']+base[4:]]
        for argv in invalid:
            result = subprocess.run(['python3', str(SCRIPT), *argv], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
