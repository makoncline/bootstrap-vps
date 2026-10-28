#!/usr/bin/env python3
"""Validate, activate, and check the managed Caddy configuration."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run(*command, timeout=15):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{command[0]} failed (exit {result.returncode})')
    return result.stdout + result.stderr


def activate(compose):
    prefix = ['docker', 'compose', '-f', str(compose)]
    container = run(*prefix, 'ps', '-q', 'caddy').strip()
    if not container:
        raise RuntimeError('Caddy is not running')
    if not run('docker', 'exec', container, 'caddy', 'version').startswith('v2.11.7 '):
        raise RuntimeError('Caddy 2.11.7 is required')
    run(*prefix, 'exec', '-T', 'caddy', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    since = datetime.now(timezone.utc).isoformat()
    run(*prefix, 'exec', '-T', 'caddy', 'sh', '-c', 'kill -USR1 1')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        logs = run('docker', 'logs', '--since', since, '--tail', '200', container)
        for line in logs.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('signal') != 'SIGUSR1':
                continue
            message = event.get('msg', '')
            if message == 'successfully reloaded config from file':
                if run(*prefix, 'ps', '-q', 'caddy').strip() != container:
                    raise RuntimeError('Caddy changed during activation')
                return
            if 'failed' in message or 'ignored' in message:
                raise RuntimeError('Caddy rejected the reload')
        time.sleep(0.1)
    raise RuntimeError('Caddy reload confirmation timed out')


def check_route(url):
    if not url:
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            run('curl', '--fail', '--silent', '--show-error', '--connect-timeout', '2', '--max-time', '3', url, timeout=4)
            return
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(0.5)
    raise RuntimeError('Route health check failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', type=Path, default=Path('/srv/stacks/proxy/compose.yaml'))
    parser.add_argument('--smoke-url', default='')
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('destination', nargs='?', type=Path)
    args = parser.parse_args()
    if bool(args.source) != bool(args.destination):
        parser.error('source and destination must be supplied together')
    lock_path = args.compose.parent / '.apply.lock'
    with lock_path.open('a') as lock:
        if os.geteuid() == 0:
            owner = args.compose.stat()
            os.chown(lock_path, owner.st_uid, owner.st_gid)
            os.chmod(lock_path, 0o660)
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = args.destination.read_bytes() if args.destination and args.destination.exists() else None
        try:
            if args.source:
                args.destination.parent.mkdir(parents=True, exist_ok=True)
                args.destination.write_bytes(args.source.read_bytes())
            activate(args.compose)
            check_route(args.smoke_url)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            if args.destination:
                if previous is None:
                    args.destination.unlink(missing_ok=True)
                else:
                    args.destination.write_bytes(previous)
                try:
                    activate(args.compose)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as recovery:
                    print(f'Previous route could not be reactivated: {recovery}', file=sys.stderr)
            print(f'Proxy activation failed: {error}', file=sys.stderr)
            return 1
    print('Proxy configuration is active')
    return 0


if __name__ == '__main__':
    sys.exit(main())
