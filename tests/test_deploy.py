#!/usr/bin/env python3
"""Run the generated deploy script with a failed pull and a cached image."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
script = source.split('write_if_changed "/usr/local/bin/bootstrap-compose-deploy.sh" <<\'EOF\'\n', 1)[1].split('\nEOF', 1)[0]
with tempfile.TemporaryDirectory(prefix='bootstrap-deploy-') as directory:
    root = Path(directory)
    (root / 'bin').mkdir()
    (root / 'stack').mkdir()
    (root / 'targets').mkdir()
    (root / 'stack/compose.yaml').write_text('services: {}\n')
    (root / 'targets/example.env').write_text(f'STACK_DIR={root}/stack\nSMOKE_URL=\n')
    env_file = root / 'stack/.env'
    log = root / 'docker.log'
    scripts = {
        'sudo': '#!/bin/sh\n[ "$1" != chown ] || exit 0\nexec "$@"\n',
        'flock': '#!/bin/sh\nexit 0\n',
        'notify': '#!/bin/sh\nexit 0\n',
        'docker': '''#!/bin/sh
operation=""
for arg do case "$arg" in pull|up) operation=$arg ;; esac; done
tag=$(cat "$TEST_STACK_ENV")
printf '%s %s\\n' "$operation" "$tag" >> "$TEST_DOCKER_LOG"
# A cached new image would let up succeed even when its pull failed.
if [ "$operation" = pull ] && [ "$tag" = 'IMAGE_TAG="new"' ]; then exit 1; fi
exit 0
''',
    }
    for name, content in scripts.items():
        path = root / 'bin' / name
        path.write_text(content)
        path.chmod(0o755)
    script = script.replace('/etc/bootstrap/deploy-hooks', str(root / 'targets')).replace('/usr/local/bin/notify', str(root / 'bin/notify'))
    deploy = root / 'deploy.sh'
    deploy.write_text(script)
    env_file.write_text('IMAGE_TAG="old"\n')
    env = dict(os.environ, PATH=f'{root}/bin:{os.environ["PATH"]}', HOME=str(root),
               TEST_STACK_ENV=str(env_file), TEST_DOCKER_LOG=str(log))
    result = subprocess.run(['bash', str(deploy), 'example', 'new'], env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'up' not in '\n'.join(line for line in log.read_text().splitlines() if '"new"' in line), log.read_text()
    assert 'up' in log.read_text(), 'The previous version was not restored'
    assert env_file.read_text() == 'IMAGE_TAG="old"\n'
    print('PASS: failed pull cannot apply a cached new image; rollback restores the prior version')
