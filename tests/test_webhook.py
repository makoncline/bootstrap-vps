#!/usr/bin/env python3
"""Exercise the generated webhook over local HTTP with disposable action commands."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
script = source.split('write_if_changed "/usr/local/bin/bootstrap-deploy-webhook.py" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
with tempfile.TemporaryDirectory(prefix='bootstrap-webhook-') as directory:
    root = Path(directory)
    action = root / 'action'
    action.write_text('''#!/bin/sh
printf '{"action_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\\n'
if [ "$2" = failed ]; then printf 'private-error-marker\\n' >&2; exit 1; fi
if [ "$2" = timeout ]; then exit 124; fi
''')
    action.chmod(0o755)
    module_path = root / 'webhook.py'
    module_path.write_text(script.replace('/usr/local/bin/bootstrap-compose-deploy.sh', str(action)))
    spec = importlib.util.spec_from_file_location('webhook', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    server.deploy_token = 'test-only-token'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(payload):
        request = Request(f'http://127.0.0.1:{server.server_port}/deploy/example', data=json.dumps(payload).encode(),
                          headers={'Authorization': 'Bearer test-only-token', 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    try:
        assert post({'image_tag': 'ok', 'clear_cache': 'false'})[0] == 400
        assert post([])[0] == 400
        code, body = post({'image_tag': 'ok'})
        assert code == 200 and body['action_id'] == 'a' * 32
        code, body = post({'image_tag': 'failed'})
        assert code == 500 and 'private-error-marker' not in json.dumps(body)
        assert post({'image_tag': 'timeout'})[0] == 504
        print('PASS: HTTP input validation, action IDs, safe failures, and timeout status')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
