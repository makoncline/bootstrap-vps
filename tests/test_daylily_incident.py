#!/usr/bin/env python3
"""Synthetic tests: no Docker, journal, network, or real identities."""
import argparse
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

    def test_verified_actor_schema(self):
        for record in ({'appUserId': 'test_app_user'}, {'clerkUserId': 'user_test_actor'},
                       {'clerk_user_id': 'user_test_actor'}, {'event': 'image_moderation', 'userId': 'test_app_user'}):
            self.assertTrue(i.actor_matches(record, IDENTITY))
            self.assertFalse(i.other_actor(record, IDENTITY))
        self.assertFalse(i.actor_matches({}, {'appUserId': 'test_app_user', 'clerkUserId': None}))
        self.assertTrue(i.other_actor({'clerk_user_id': 'user_other'}, IDENTITY))

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
        with patch.object(i, 'journal', journal), patch.object(i, 'proxy', lambda _: iter(proxies)), \
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
