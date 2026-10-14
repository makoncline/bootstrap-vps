#!/usr/bin/env python3
"""Exercise the actual tracked encoder in a disposable, network-isolated Caddy."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProxyLogRedactionTest(unittest.TestCase):
    def test_raw_access_records_keep_paths_and_remove_queries_and_credentials(self):
        template = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
        config = template.split('write_if_changed "/srv/stacks/proxy/Caddyfile" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
        config = config.replace('/var/log/caddy/access-retained.log', '/tmp/access.log')
        config = config.replace('  import /etc/caddy/sites/*.caddy\n', '')
        config = config.replace('respond "unconfigured host" 404', 'respond "fixture" 200')
        commands = r'''
set -eu
cat > /tmp/Caddyfile
caddy validate --config /tmp/Caddyfile --adapter caddyfile >/dev/null 2>&1
caddy run --config /tmp/Caddyfile --adapter caddyfile >/dev/null 2>/tmp/runtime.log &
caddy_pid=$!
trap 'kill "$caddy_pid" 2>/dev/null || true; wait "$caddy_pid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 50); do
  if wget -q -O /dev/null http://127.0.0.1/ready; then break; fi
  sleep 0.1
done
wget -q -O /dev/null \
  --header='Authorization: Bearer synthetic-authorization' \
  --header='X-Deploy-Token: synthetic-deploy' \
  --header='Cookie: session=synthetic-cookie' \
  --header='Set-Cookie: synthetic-set-cookie' \
  'http://127.0.0.1/api/trpc/synthetic.get?input=%7B%22value%22%3A%22synthetic-query%22%7D&other=synthetic-other'
wget -q -O /dev/null 'http://127.0.0.1/catalogs'
kill "$caddy_pid"
wait "$caddy_pid"
trap - EXIT
cat /tmp/access.log
'''
        result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none',
                                 '--entrypoint', 'sh', 'caddy:2', '-c', commands],
                                input=config, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        matched = [r for r in records if r['request']['uri'].startswith('/api/trpc/')]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]['request']['uri'], '/api/trpc/synthetic.get')
        self.assertEqual(matched[0]['status'], 200)
        self.assertTrue(any(r['request']['uri'] == '/catalogs' for r in records))
        headers = {name.lower() for name in matched[0]['request']['headers']}
        self.assertFalse(headers & {'authorization', 'x-deploy-token', 'cookie', 'set-cookie'})
        for value in ('synthetic-query', 'synthetic-other', 'synthetic-authorization',
                      'synthetic-deploy', 'synthetic-cookie', 'synthetic-set-cookie'):
            self.assertNotIn(value, result.stdout)


if __name__ == '__main__':
    unittest.main()
