#!/usr/bin/env python3
"""Check real journal storage across disposable container replacement."""
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.check_output(['docker', *args], text=True, **kwargs).strip()


endpoint = docker('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
if not os.environ.get('DOCKER_CONTEXT'):
    endpoint = os.environ.get('DOCKER_HOST', endpoint)
assert endpoint.startswith('unix://'), 'Select a local Docker engine.'
name = 'bootstrap-journal-' + uuid.uuid4().hex[:10]
volume = name + '-data'
machine = uuid.uuid4().hex
source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
settings = source.split('cat > "$settings" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
try:
    docker('volume', 'create', volume)
    with tempfile.TemporaryDirectory(prefix='bootstrap-journal-') as directory:
        config = Path(directory) / 'journal.conf'
        config.write_text(settings)
        for iteration in range(2):
            docker('run', '-d', '--name', name, '--tmpfs', '/run', '-v', volume + ':/var/log/journal', 'bootstrap-ssh-test:local')
            docker('exec', name, 'mkdir', '-p', '/etc/systemd/journald.conf.d')
            docker('cp', str(config), name + ':/etc/systemd/journald.conf.d/60-bootstrap.conf')
            docker('exec', name, 'bash', '-c', '''
set -eu
printf '%s\\n' "$1" > /etc/machine-id
/lib/systemd/systemd-journald > /tmp/journal-start.log 2>&1 &
for attempt in $(seq 1 50); do
  [ ! -S /run/systemd/journal/socket ] || exit 0
  sleep 0.1
done
cat /tmp/journal-start.log
exit 1
''', 'test', machine)
            docker('exec', name, 'journalctl', '--flush')
            if iteration == 0:
                docker('exec', name, 'mkdir', '-p', '/usr/local/lib/bootstrap')
                for script in ('run_action.py', 'bootstrapctl.py'):
                    docker('cp', str(ROOT / 'bootstrap/remote' / script), name + ':/usr/local/lib/bootstrap/' + script)
                docker('exec', name, 'chmod', '755', '/usr/local/lib/bootstrap/bootstrapctl.py')
                docker('exec', name, 'ln', '-s', '/usr/local/lib/bootstrap/bootstrapctl.py', '/usr/local/bin/bootstrapctl')
                import json
                report = json.loads(docker('exec', name, 'bootstrapctl', 'status'))
                assert report['incomplete'] and report['sections']['docker']['status'] == 'unavailable'

            if iteration == 0:
                docker('exec', '-i', name, 'logger', '--journald', input='SYSLOG_IDENTIFIER=bootstrap-test\nMESSAGE=persistent-test-evidence\n')
                docker('exec', name, 'journalctl', '--sync')
            else:
                retained = docker('exec', name, 'journalctl', '--directory', '/var/log/journal', '-t', 'bootstrap-test', '--no-pager')
                assert 'persistent-test-evidence' in retained, retained
            docker('rm', '-f', name)
    print('PASS: journal evidence survives container replacement; host reboot is not simulated')
finally:
    subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['docker', 'volume', 'rm', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
