#!/usr/bin/env python3
"""Exercise config updates and unauthenticated checks through a local HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'bootstrap/remote/ai-proxy.py'


class Proxy(BaseHTTPRequestHandler):
    status = 200
    model = 'test-model'
    requests = []

    def do_GET(self):
        self.requests.append((self.path, dict(self.headers)))
        self.send_response(self.status)
        if self.status == 302:
            self.send_header('Location', '/must-not-follow')
        self.end_headers()
        self.wfile.write(json.dumps({'data': [{'id': self.model}],
                                   'error': 'private-error-marker'}).encode())

    def log_message(self, *args):
        pass


with tempfile.TemporaryDirectory(prefix='bootstrap-ai-') as directory:
    config = Path(directory) / 'config.toml'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = dict(os.environ, AI_PROXY_URL=f'http://127.0.0.1:{server.server_port}/v1/',
               AI_PROXY_MODEL='test-model', OPENAI_API_KEY='must-not-send',
               HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1')

    def run(command, **values):
        return subprocess.run([sys.executable, str(SCRIPT), command, '--config', str(config)],
                              env=dict(env, **values), capture_output=True, text=True, timeout=10)

    try:
        assert run('configure', AI_PROXY_URL='', AI_PROXY_MODEL='').returncode == 0
        assert not config.exists()
        for url, model in [('', 'test-model'), (env['AI_PROXY_URL'], ''),
                           ('https://example.com/v1', 'test-model'),
                           ('http://user:private-error-marker@100.64.0.2:8317/v1', 'test-model'),
                           ('http://100.64.0.2:8317/v1?key=private-error-marker', 'test-model')]:
            result = run('configure', AI_PROXY_URL=url, AI_PROXY_MODEL=model)
            assert result.returncode != 0 and not config.exists()
            assert 'private-error-marker' not in result.stdout + result.stderr
        for url in ['http://proxy.example.ts.net:8317/v1', 'http://100.64.0.2:8317/v1',
                    'http://[fd7a:115c:a1e0::2]:8317/v1']:
            assert run('validate', AI_PROXY_URL=url).returncode == 0

        config.write_text('# Keep this comment.\nmodel = "old-model"\n'
                          '[sandbox_workspace_write]\nnetwork_access = true\n'
                          '[projects."/srv/project"]\ntrust_level = "trusted"\n'
                          '[model_providers.other]\nname = "Other"\n'
                          '[model_providers.cli_proxy]\nenv_key = "OLD_KEY"\n'
                          'http_headers = { Authorization = "old-secret" }\n')
        config.chmod(0o600)
        assert run('configure').returncode == 0
        content = config.read_text()
        parsed = tomllib.loads(content)
        assert '# Keep this comment.' in content and 'old-secret' not in content
        assert parsed['sandbox_workspace_write']['network_access']
        assert parsed['projects']['/srv/project']['trust_level'] == 'trusted'
        assert parsed['model_providers']['other']['name'] == 'Other'
        assert parsed['model_provider'] == 'cli_proxy' and parsed['model'] == 'test-model'
        provider = parsed['model_providers']['cli_proxy']
        assert not provider['requires_openai_auth'] and 'env_key' not in provider
        assert config.stat().st_mode & 0o777 == 0o600
        modified = config.stat().st_mtime_ns
        assert run('configure').returncode == 0
        assert config.read_text() == content and config.stat().st_mtime_ns == modified
        assert run('configure', AI_PROXY_URL='', AI_PROXY_MODEL='').returncode == 0
        assert config.read_text() == content

        result = run('check')
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['inference_verified'] is False
        assert Proxy.requests[-1][0] == '/v1/models'
        assert 'Authorization' not in Proxy.requests[-1][1]
        Proxy.model = 'other-model'
        assert run('check').returncode != 0
        for status in [401, 302]:
            Proxy.status = status
            count = len(Proxy.requests)
            result = run('check')
            assert result.returncode != 0 and str(status) in result.stderr
            assert 'private-error-marker' not in result.stdout + result.stderr
            assert len(Proxy.requests) == count + 1

        assert run('configure', AI_PROXY_URL='http://100.64.0.3:8317/v1',
                   AI_PROXY_MODEL='replacement-model').returncode == 0
        parsed = tomllib.loads(config.read_text())
        assert parsed['model'] == 'replacement-model'
        assert parsed['model_providers']['cli_proxy']['base_url'] == 'http://100.64.0.3:8317/v1'
        config.write_text('model = "private-error-marker\n')
        result = run('configure')
        assert result.returncode != 0 and 'private-error-marker' not in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

print('PASS: proxy config preservation, validation, no credentials, and bounded model checks')
