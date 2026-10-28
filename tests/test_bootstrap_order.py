#!/usr/bin/env python3
"""Run bootstrap orchestration with public routing absent until Cloudflare setup."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'bootstrap/remote/init-host.sh').read_text()
activation = source.split('log "Starting Docker stacks"\n', 1)[1].split(
    'log "Installing monitoring scripts and timer"', 1)[0]

with tempfile.TemporaryDirectory(prefix='bootstrap-order-') as directory:
    root = Path(directory)
    tools = root / 'bin'
    tools.mkdir()

    def executable(path, text):
        path.write_text(text)
        path.chmod(0o755)

    executable(tools / 'ssh', '''#!/usr/bin/env python3
import os, pathlib, subprocess, sys
command = sys.argv[-1]
state = pathlib.Path(os.environ['TEST_STATE'])
if 'tar -xzf' in command:
    sys.stdin.buffer.read()
elif 'init-host.sh' in command:
    raise SystemExit(subprocess.call(['bash', os.environ['TEST_ACTIVATION']]))
elif 'lockdown-ssh.sh' in command:
    (state / 'locked').touch()
elif command == 'true' and not sys.argv[-2].endswith('@server-example'):
    raise SystemExit(255 if (state / 'locked').exists() else 0)
''')
    executable(tools / 'scp', '#!/bin/sh\nexit 0\n')
    executable(tools / 'git', '#!/bin/sh\nprintf "test-revision\\n"\n')
    executable(tools / 'curl', '''#!/bin/sh
printf 'smoke\n' >> "$TEST_STATE/events"
test -f "$TEST_STATE/public-ready"
''')
    executable(root / 'cloudflare', '''#!/bin/sh
printf 'cloudflare\n' >> "$TEST_STATE/events"
touch "$TEST_STATE/public-ready"
''')
    executable(root / 'apply-proxy', '''#!/bin/sh
printf 'activation\n' >> "$TEST_STATE/events"
if [ "${1:-}" = --smoke-url ]; then curl "$2"; fi
''')
    (root / 'activation.sh').write_text(
        'set -euo pipefail\n'
        'start_stacks() { printf "stacks\\n" >> "$TEST_STATE/events"; }\n' +
        activation.replace('/usr/local/bin/bootstrap-apply-proxy', shlex.quote(str(root / 'apply-proxy'))))
    local = (ROOT / 'bootstrap/local/bootstrap-host.sh').read_text()
    local = local.replace('SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)',
                          'SCRIPT_DIR=' + shlex.quote(str(ROOT / 'bootstrap/local')))
    local = local.replace('"$SCRIPT_DIR/cloudflare-tunnel.sh"', shlex.quote(str(root / 'cloudflare')))
    executable(root / 'bootstrap.sh', local)
    (root / 'key').write_text('test-private-key\n')
    (root / 'key.pub').write_text('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey tests\n')
    (root / 'host.env').write_text('''HOST_PUBLIC_IPV4=192.0.2.10
HOSTNAME=server-example
ADMIN_USER=admin
TAILSCALE_AUTHKEY=test-key
TAILSCALE_HOSTNAME=server-example
TUNNEL_TOKEN=test-token
TUNNEL_HOSTNAMES=whoami.example.test
SMOKE_HOSTNAME=whoami.example.test
AI_PROXY_URL=
AI_PROXY_MODEL=
''')
    for skip in ('0', '1'):
        state = root / ('state-' + skip)
        state.mkdir()
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   BOOTSTRAP_SSH_KEY=str(root / 'key'), BOOTSTRAP_SKIP_CLOUDFLARE='0',
                   BOOTSTRAP_SKIP_SMOKE_CHECK=skip, BOOTSTRAP_SKIP_LOCKDOWN='0',
                   TEST_STATE=str(state), TEST_ACTIVATION=str(root / 'activation.sh'))
        result = subprocess.run(['bash', str(root / 'bootstrap.sh'), str(root / 'host.env')],
                                env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        events = (state / 'events').read_text().splitlines()
        assert events == ['stacks', 'activation', 'cloudflare'] + ([] if skip == '1' else ['smoke']), events
        assert (state / 'locked').exists()

print('PASS: activation precedes Cloudflare setup; public smoke follows setup and honors its skip flag')
