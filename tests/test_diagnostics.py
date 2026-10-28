#!/usr/bin/env python3
"""Run diagnostics with missing tools, stale setup state, large logs, and timeouts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bootstrap-diagnostics-') as directory:
    root = Path(directory)
    tools = root / 'bin'
    tools.mkdir()
    state = root / 'state'
    state.mkdir()
    (root / 'config').mkdir()
    (root / 'config/manifest.json').write_text('{"revision":"test-revision"}')
    (state / 'last-setup.json').write_text('{"phase":"bootstrap","status":"running","pid":999999,"boot_id":"previous-boot"}')
    (state / 'last-action.json').write_text('{"phase":"deploy","status":"failed","exit_code":7}')
    runner = (ROOT / 'bootstrap/remote/run_action.py').read_text()
    (root / 'run_action.py').write_text(runner)
    source = (ROOT / 'bootstrap/remote/bootstrapctl.py').read_text()
    for original, replacement in [('/var/lib/bootstrap', state), ('/srv/stacks', root / 'stacks'), ('/etc/bootstrap', root / 'config'), ('/proc', root / 'proc')]:
        source = source.replace(f"Path('{original}')", f'Path({str(replacement)!r})')
    source = source.replace('REPORT_SECONDS = 60', 'REPORT_SECONDS = 3')
    command = root / 'bootstrapctl.py'
    command.write_text(source)
    for name in ('systemctl', 'df', 'free', 'timedatectl', 'codex', 'tailscale'):
        path = tools / name
        path.write_text('#!/bin/sh\n[ "${TEST_BLOCK:-0}" = 0 ] || exec sleep 60\nprintf "test state\\n"\n' if name == 'df' else '#!/bin/sh\nprintf "test state\\n"\n')
        path.chmod(0o755)
    docker = tools / 'docker'
    docker.write_text('#!/bin/sh\n[ "${TEST_BLOCK:-0}" = 0 ] || exec sleep 60\nprintf "test Docker version\\n"\n')
    docker.chmod(0o755)
    journal = tools / 'journalctl'
    journal.write_text('#!/bin/sh\nprintf "%s\\n" "$EXAMPLE_TOKEN"\npython3 -c \'print("x" * 200000)\'\n')
    journal.chmod(0o755)
    env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', EXAMPLE_TOKEN='private-diagnostic-marker')

    def report(operation, **extra):
        command.write_text(source.replace('REPORT_SECONDS = 3', 'REPORT_SECONDS = 0.5') if extra.get('TEST_BLOCK') else source)
        result = subprocess.run([sys.executable, str(command), operation], env={**env, **extra}, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert len(result.stdout.encode()) <= 1024 * 1024
        assert 'private-diagnostic-marker' not in result.stdout
        return json.loads(result.stdout)

    data = report('diagnose')
    assert data['sections']['manifest']['value']['revision'] == 'test-revision'
    assert data['sections']['last_setup']['value']['status'] == 'interrupted'
    assert any(section['status'] == 'truncated' for section in data['sections'].values())
    assert data['incomplete']
    data = report('status', TEST_BLOCK='1')
    assert any(section['status'] == 'timed_out' for section in data['sections'].values())
    assert data['incomplete']
    (root / 'config/manifest.json').write_text('[]')
    assert report('status')['sections']['manifest']['status'] == 'invalid'
    print('PASS: stale setup detection, invalid state, output bounds, redaction, and command deadlines')
