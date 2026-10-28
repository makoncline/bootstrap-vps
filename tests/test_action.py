#!/usr/bin/env python3
"""Check action results, output bounds, redaction, and timeout with real children."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bootstrap-action-') as directory:
    root = Path(directory)
    tools = root / 'bin'
    tools.mkdir()
    journal = root / 'journal'
    logger = tools / 'logger'
    logger.write_text(f'#!/bin/sh\ncat >> "{journal}"\n')
    logger.chmod(0o755)
    runner = root / 'runner.py'
    source = (ROOT / 'bootstrap/remote/run-action.py').read_text()
    config = root / 'config'
    config.mkdir()
    writer = (ROOT / 'bootstrap/local/bootstrap-host.sh').read_text().split('append_env() {\n', 1)[1].split('\n}', 1)[0]
    subprocess.run(['bash', '-c', 'append_env() {\n' + writer + '\n}\n'
                    'append_env OPTIONAL_TOKEN ""\nappend_env OTHER_TOKEN private-marker-123\n'],
                   env=dict(os.environ, RUNTIME_ENV_FILE=str(config / 'server.env')), check=True)
    runner.write_text(source.replace("Path('/var/lib/bootstrap')", f'Path({str(root / "state")!r})')
                      .replace("Path('/etc/bootstrap')", f'Path({str(config)!r})')
                      .replace('ACTION_TIMEOUT = 600', 'ACTION_TIMEOUT = 0.5')
                      .replace('OUTPUT_LIMIT = 1024 * 1024', 'OUTPUT_LIMIT = 8192'))
    namespace = {'__name__': 'test_redaction'}
    exec(compile(runner.read_text(), str(runner), 'exec'), namespace)
    with patch.dict(os.environ, {}, clear=True):
        secrets = namespace['secret_values']()
        assert '' not in secrets and 'private-marker-123' in secrets
        state = namespace['redact']('{"status":"succeeded","value":"private-marker-123"}', secrets)
        assert json.loads(state) == {'status': 'succeeded', 'value': '[redacted]'}
    env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', EXAMPLE_TOKEN='private-marker-123')

    def action(code):
        result = subprocess.run([sys.executable, str(runner), 'deploy', 'example', sys.executable, '-c', code],
                                capture_output=True, text=True, env=env, timeout=10)
        record = json.loads((root / 'state/last-action.json').read_text())
        assert record == json.loads(result.stdout)
        return result, record

    result, record = action('import os; print(os.environ["EXAMPLE_TOKEN"]); print("phase=apply"); raise SystemExit(7)')
    assert result.returncode == 7 and record['status'] == 'failed'
    assert 'phase=apply' in journal.read_text()
    assert 'private-marker-123' not in journal.read_text()
    journal.write_text('')
    result, noisy = action('for i in range(100): print("log line", i)\n'
                          'print("[bootstrap] " + "\\U0001f680" * 160)\nraise SystemExit(7)')
    assert result.returncode == 7 and noisy['output_truncated']
    assert len(journal.read_bytes()) <= 8192
    _, next_action = action('print("next action")')
    assert json.loads((root / 'state/last-action.json').read_text())['action_id'] == next_action['action_id']
    entries = [json.loads(line.removeprefix('MESSAGE=')) for line in journal.read_text().splitlines()
               if line.startswith('MESSAGE=')]
    terminal = [entry for entry in entries if entry['action_id'] == noisy['action_id'] and entry['message'] == 'finished']
    assert len(terminal) == 1, 'The noisy action lost its terminal journal result'
    assert terminal[0]['status'] == 'failed' and terminal[0]['exit_code'] == 7 and terminal[0]['output_truncated']
    assert terminal[0]['step'] == '\U0001f680' * 160
    result, record = action('print("x" * 20000)')
    assert result.returncode == 0 and record['output_truncated']
    result, record = action('import time; print("started", flush=True); time.sleep(60)')
    assert result.returncode == 124 and record['status'] == 'timed_out'
    assert record['elapsed_seconds'] < 8
    assert (root / 'state/last-action.json').stat().st_mode & 0o777 == 0o640

    real_git = shutil.which('git')
    repo = root / 'git-repo'
    files = repo / 'deploy/vps'
    files.mkdir(parents=True)
    (files / 'compose.yaml').write_text('services: {}\n')
    (files / 'caddy-route.caddy').write_text('respond "fixture"\n')

    def git(*args):
        subprocess.run([real_git, *map(str, args)], capture_output=True, check=True)

    git('init', '-q', repo)
    git('-C', repo, 'add', '.')
    git('-C', repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture')
    checkout = root / 'checkout'
    git('clone', '-q', repo, checkout)
    git('-C', checkout, 'fetch', '-q', 'origin', 'HEAD')
    sync_stack = root / 'sync-stack'
    sync_stack.mkdir()
    (sync_stack / '.env').write_text('')
    sync_targets = root / 'sync-targets'
    sync_targets.mkdir()
    (sync_targets / 'example.env').write_text(f'REPO_URL={repo}\nCHECKOUT_DIR={checkout}\nSTACK_DIR={sync_stack}\n')
    apply_log = root / 'sync-apply.log'
    fixtures = {
        'git': f'''#!/usr/bin/env python3
import subprocess, sys
result = subprocess.run([{real_git!r}, *sys.argv[1:]])
if 'fetch' in sys.argv and result.returncode:
    print('remote: https://operator:git-private-marker@example.invalid/repo?token=git-query-marker', file=sys.stderr)
    print('remote: https://example.invalid?token=prefix@git-query-boundary-marker', file=sys.stderr)
raise SystemExit(result.returncode)
''',
        'flock': '#!/bin/sh\nexit 0\n',
        'docker': '#!/bin/sh\nprintf "docker\\n" >> "$TEST_APPLY_LOG"\n',
        'notify': '#!/bin/sh\nprintf "notify\\n" >> "$TEST_APPLY_LOG"\n',
        'apply': '#!/bin/sh\nprintf "proxy\\n" >> "$TEST_APPLY_LOG"\n',
    }
    for name, content in fixtures.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    sync_source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
    sync = sync_source.split('write_if_changed "/usr/local/bin/bootstrap-sync-config.sh" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
    sync = sync.replace('/etc/bootstrap/config-sync', str(sync_targets)).replace('/usr/local/bin/notify', str(tools / 'notify')).replace('/usr/local/bin/bootstrap-apply-proxy', str(tools / 'apply'))
    (root / 'sync.sh').write_text(sync)
    git_runner = root / 'git-runner.py'
    git_runner.write_text(runner.read_text().replace('ACTION_TIMEOUT = 0.5', 'ACTION_TIMEOUT = 10'))
    journal.write_text('')
    git_env = dict(env, HOME=str(root), LC_ALL='C', TEST_APPLY_LOG=str(apply_log))
    git_env.pop('BOOTSTRAP_ACTION_ID', None)
    result = subprocess.run([sys.executable, str(git_runner), 'config-sync', 'example', 'bash',
                             str(root / 'sync.sh'), 'example', 'missing-review-ref'],
                            env=git_env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and json.loads(result.stdout)['status'] == 'failed'
    assert not apply_log.exists(), 'A failed fetch reached an apply or notification step'
    raw = journal.read_text()
    assert "couldn't find remote ref missing-review-ref" in raw, 'The Git error was lost'
    assert 'git-private-marker' not in raw and 'git-query-marker' not in raw, 'Git stderr exposed a URL credential'
    assert 'git-query-boundary-marker' not in raw, 'Git stderr exposed an empty-path query secret'
    for name in fixtures:
        (tools / name).unlink()
    print('PASS: failed fetch retains its sanitized cause and stops before application')

    if sys.platform == 'linux':
        stack = root / 'stack'
        stack.mkdir()
        (stack / 'compose.yaml').write_text('services: {}\n')
        targets = root / 'targets'
        targets.mkdir()
        live = root / 'live-tag'
        docker_log = root / 'docker.log'
        notifications = root / 'notifications'

        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                status = 200
                if live.read_text() == 'new':
                    time.sleep(6)
                    status = 503
                try:
                    self.send_response(status)
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        health = ThreadingHTTPServer(('127.0.0.1', 0), Health)
        worker = threading.Thread(target=health.serve_forever, daemon=True)
        worker.start()
        (targets / 'example.env').write_text(f'STACK_DIR={stack}\nSMOKE_URL=http://127.0.0.1:{health.server_port}/health\n')
        fixtures = {
            'sudo': '#!/bin/sh\n[ "$1" != chown ] || exit 0\nexec "$@"\n',
            'notify': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_NOTIFICATIONS"\n',
            'docker': '''#!/usr/bin/env python3
import os, pathlib, signal, sys, time
operation = next(arg for arg in sys.argv if arg in ('pull', 'up', 'logs'))
tag = pathlib.Path(os.environ['TEST_STACK_ENV']).read_text().split('"')[1]
with open(os.environ['TEST_DOCKER_LOG'], 'a') as log:
    log.write(operation + ' ' + tag + '\\n')
if operation == 'up':
    pathlib.Path(os.environ['TEST_LIVE_TAG']).write_text(tag)
if tag == 'new' and operation == os.environ['TEST_STALL']:
    if operation == 'logs':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(30)
    time.sleep(6)
''',
        }
        for name, content in fixtures.items():
            path = tools / name
            path.write_text(content)
            path.chmod(0o755)
        deploy_source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
        deploy = deploy_source.split('write_if_changed "/usr/local/bin/bootstrap-compose-deploy.sh" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
        deploy = deploy.replace('/etc/bootstrap/deploy-hooks', str(targets)).replace('/usr/local/bin/notify', str(tools / 'notify'))
        deploy = deploy.replace('SECONDS + 300', 'SECONDS + 2').replace('SECONDS + 120', 'SECONDS + 3').replace('--kill-after=5', '--kill-after=0.2')
        deploy = deploy.replace(' 10 env -u IMAGE_TAG', ' 1 env -u IMAGE_TAG')
        (root / 'deploy.sh').write_text(deploy)
        deadline_runner = root / 'deadline-runner.py'
        deadline_runner.write_text(source.replace("Path('/var/lib/bootstrap')", f'Path({str(root / "state")!r})').replace('ACTION_TIMEOUT = 600', 'ACTION_TIMEOUT = 8'))
        try:
            for stall in ('logs', 'health', 'pull', 'up'):
                live.write_text('old')
                (stack / '.env').write_text('IMAGE_TAG="old"\n')
                docker_log.write_text('')
                notifications.write_text('')
                case_env = dict(env, HOME=str(root), NO_PROXY='127.0.0.1', TEST_STALL=stall,
                                TEST_STACK_ENV=str(stack / '.env'), TEST_LIVE_TAG=str(live),
                                TEST_DOCKER_LOG=str(docker_log), TEST_NOTIFICATIONS=str(notifications))
                case_env.pop('BOOTSTRAP_ACTION_ID', None)
                result = subprocess.run([sys.executable, str(deadline_runner), 'deploy', 'example',
                                         'bash', str(root / 'deploy.sh'), 'example', 'new'],
                                        env=case_env, capture_output=True, text=True, timeout=15)
                record = json.loads(result.stdout)
                assert result.returncode == 1 and record['status'] == 'failed', (stall, result.stdout, result.stderr)
                assert live.read_text() == 'old' and (stack / '.env').read_text() == 'IMAGE_TAG="old"\n'
                assert 'rollback: successful' in notifications.read_text()
                assert 'up old' in docker_log.read_text() and 'pull old' not in docker_log.read_text()
        finally:
            health.shutdown()
            health.server_close()
            worker.join()
        print('PASS: stalled logs, health, pull, and startup leave time for cached rollback through the real action wrapper')
    print('PASS: saved results, journal output, secret redaction, truncation, and child timeout')
