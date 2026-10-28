#!/usr/bin/env python3
"""Run health checks with a failed alert, retry, and recovery."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
script = source.split('write_if_changed "/usr/local/bin/bootstrap-healthcheck.sh" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
with tempfile.TemporaryDirectory(prefix='bootstrap-health-') as directory:
    root = Path(directory)
    tools = root / 'bin'
    tools.mkdir()
    state = root / 'state'
    script = script.replace('/var/lib/bootstrap-health', str(state)).replace('/usr/local/bin/bootstrap-alert.sh', str(tools / 'alert'))
    (root / 'health.sh').write_text(script)
    (root / 'host.env').write_text('HOSTNAME=example\nSMOKE_HOSTNAME=app.example.com\n')
    stubs = {
        'timeout': '#!/bin/sh\nshift\nexec "$@"\n',
        'systemctl': '#!/bin/sh\n[ "$*" != "is-active --quiet docker" ] || [ "$TEST_HEALTH" = ok ]\n',
        'tailscale': '#!/bin/sh\nprintf \'{"BackendState":"Running"}\\n\'\n',
        'docker': '#!/bin/sh\nprintf "caddy\\ncloudflared\\n"\n',
        'curl': '#!/bin/sh\nexit 0\n',
        'df': '#!/bin/sh\nprintf "Use%%\\n10%%\\n"\n',
        'alert': f'#!/bin/sh\nprintf "attempt\\n" >> "{root}/attempts"\n[ "$TEST_ALERT" = ok ]\n',
    }
    for name, content in stubs.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)

    def check(health, alert):
        env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', TEST_HEALTH=health, TEST_ALERT=alert)
        return subprocess.run(['bash', str(root / 'health.sh'), str(root / 'host.env')], env=env, capture_output=True, text=True)

    first = check('failed', 'failed')
    assert first.returncode == 1 and 'docker service is not active' in first.stdout
    assert not (state / 'last-alert-state').exists()
    assert (state / 'current-state').read_text().startswith('fail:')
    assert check('failed', 'ok').returncode == 1
    assert (state / 'last-alert-state').read_text().startswith('fail:')
    assert check('failed', 'ok').returncode == 1
    assert len((root / 'attempts').read_text().splitlines()) == 2
    assert check('ok', 'ok').returncode == 0
    assert (state / 'current-state').read_text() == 'ok\n'
    assert len((root / 'attempts').read_text().splitlines()) == 3
    print('PASS: local health evidence, failed-alert retry, deduplication, and recovery')
