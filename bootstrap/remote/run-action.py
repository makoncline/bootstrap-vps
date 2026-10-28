#!/usr/bin/env python3
"""Run one host action with bounded journal output and a saved result."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid

STATE_DIR = Path('/var/lib/bootstrap')
ACTION_TIMEOUT = 600
OUTPUT_LIMIT = 1024 * 1024
SECRET_NAME = re.compile(r'token|secret|password|authkey|api_key', re.I)


def utc():
    return datetime.now(timezone.utc).isoformat()


def secret_values():
    values = [value for name, value in os.environ.items() if SECRET_NAME.search(name) and value]
    for path in Path('/etc/bootstrap').glob('*.env'):
        try:
            for line in path.read_text().splitlines():
                name, separator, value = line.partition('=')
                if separator and SECRET_NAME.search(name):
                    values.extend(shlex.split(value))
        except (OSError, ValueError):
            continue
    return sorted(set(values) - {''}, key=len, reverse=True)


def redact(text, secrets):
    for value in secrets:
        text = text.replace(value, '[redacted]')
    text = re.sub(r'(?i)(https?://)[^/?#\s]+@', r'\1[redacted]@', text)
    text = re.sub(r'(?i)(authorization[:=]\s*(?:bearer\s+)?)[^\s,;]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:token|secret|password|api_key)["\s]*[:=]["\s]*)[^\s,";]+', r'\1[redacted]', text)
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', text)


def save(result):
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o750)
    with tempfile.NamedTemporaryFile(mode='w', dir=STATE_DIR, delete=False) as handle:
        os.chmod(handle.name, 0o640)
        json.dump(result, handle)
    os.replace(handle.name, STATE_DIR / 'last-action.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['deploy', 'config-sync', 'bootstrap', 'reconcile'])
    parser.add_argument('target')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9._-]+', args.target) or not args.command:
        parser.error('a valid target and command are required')
    action = {'action_id': uuid.uuid4().hex, 'phase': args.phase, 'target': args.target,
              'started_at': utc(), 'status': 'running', 'journal_available': True}
    secrets = secret_values()
    written = 0

    def event(message, terminal=False):
        nonlocal written
        message = redact(message, secrets)
        if message.startswith('[bootstrap] '):
            action['step'] = message.removeprefix('[bootstrap] ')[:160]
            save(action)
        record = json.dumps({**action, 'at': utc(), 'message': message})
        payload = f'SYSLOG_IDENTIFIER=bootstrap-action\nMESSAGE={record}\n'
        size = len(payload.encode())
        limit = OUTPUT_LIMIT
        if not terminal:
            # Reserve the action metadata plus room for the final status and step.
            limit -= len(json.dumps(action).encode()) + 4096
        if written + size > limit:
            action['output_truncated'] = True
            return
        written += size
        if not action['journal_available']:
            return
        try:
            result = subprocess.run(['logger', '--journald'], input=payload,
                                    text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            action['journal_available'] = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            action['journal_available'] = False

    event('started')
    save(action)
    started = time.monotonic()
    try:
        child = subprocess.Popen(args.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 start_new_session=True, env={**os.environ, 'BOOTSTRAP_ACTION_ID': action['action_id']})
    except OSError:
        action.update(status='failed', exit_code=127, finished_at=utc())
        event('could not start action', terminal=True)
        save(action)
        print(json.dumps(action))
        return 127
    pending = b''
    skipping_line = False
    timed_out = False
    interrupted = False

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map() or child.poll() is None:
                if interrupted or time.monotonic() - started >= ACTION_TIMEOUT:
                    timed_out = not interrupted
                    break
                for key, _ in selector.select(timeout=0.2):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if not skipping_line:
                            event(line.decode(errors='replace'))
                        skipping_line = False
                    if len(pending) > 8192:
                        pending = b''
                        skipping_line = True
                        action['output_truncated'] = True
            if pending and not skipping_line:
                event(pending.decode(errors='replace'))
        if timed_out or interrupted:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
        code = child.wait(timeout=5)
    finally:
        child.stdout.close()
    action.update(status='interrupted' if interrupted else ('timed_out' if timed_out else ('succeeded' if code == 0 else 'failed')),
                  exit_code=143 if interrupted else (124 if timed_out else code), finished_at=utc(), elapsed_seconds=round(time.monotonic() - started, 2))
    event('finished', terminal=True)
    save(action)
    print(json.dumps(action))
    return action['exit_code']


if __name__ == '__main__':
    sys.exit(main())
