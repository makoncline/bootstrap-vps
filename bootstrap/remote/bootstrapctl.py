#!/usr/bin/env python3
"""Read bounded host status and logs. This command makes no repairs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

from run_action import redact, secret_values

STATE_DIR = Path('/var/lib/bootstrap')
STACKS_DIR = Path('/srv/stacks')
CONFIG_DIR = Path('/etc/bootstrap')
PROC_DIR = Path('/proc')
REPORT_SECONDS = 60


class Report:
    def __init__(self):
        self.deadline = time.monotonic() + REPORT_SECONDS
        self.remaining = 128 * 1024
        self.secrets = secret_values()
        self.sections = {}

    def command(self, name, command):
        result = {'status': 'ok', 'output': ''}
        self.sections[name] = result
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            result['status'] = 'limit'
            return result
        try:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError:
            result['status'] = 'unavailable'
            return result
        output = bytearray()
        deadline = min(self.deadline, time.monotonic() + 5)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map() or child.poll() is None:
                    if time.monotonic() >= deadline:
                        result['status'] = 'timed_out'
                        break
                    for key, _ in selector.select(timeout=0.1):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) >= min(32768, self.remaining):
                            result['status'] = 'truncated'
                            break
                    if result['status'] != 'ok':
                        break
            child.stdout.close()
            try:
                code = child.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = child.wait(timeout=1)
        finally:
            child.stdout.close()
        self.remaining -= len(output)
        result['output'] = redact(output.decode(errors='replace'), self.secrets)
        result['exit_code'] = code
        no_entries = command[0] == 'journalctl' and code == 1 and result['output'].strip() == '-- No entries --'
        if result['status'] == 'ok' and code and not no_entries:
            result['status'] = 'failed'
        return result

    def file(self, name, path):
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            self.sections[name] = {'status': 'limit'}
            return
        cap = min(32768, self.remaining)
        try:
            with path.open('rb') as handle:
                data = handle.read(cap + 1)
            self.remaining -= len(data)
            value = redact(data[:cap].decode(errors='replace'), self.secrets)
            self.sections[name] = {'status': 'truncated' if len(data) > cap else 'ok', 'output': value}
        except PermissionError:
            self.sections[name] = {'status': 'denied'}
        except OSError:
            self.sections[name] = {'status': 'unavailable'}

    def state(self, name, path):
        self.file(name, path)
        section = self.sections[name]
        if section['status'] != 'ok':
            return
        try:
            value = json.loads(section.pop('output'))
        except ValueError:
            section['status'] = 'invalid'
            return
        if not isinstance(value, dict):
            section['status'] = 'invalid'
            return
        if value.get('status') == 'running':
            try:
                boot = (PROC_DIR / 'sys/kernel/random/boot_id').read_text().strip()
                process = (PROC_DIR / str(int(value['pid'])) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
                if value['boot_id'] != boot or value['process_start'] != process:
                    value['status'] = 'interrupted'
            except (OSError, KeyError, TypeError, ValueError):
                value['status'] = 'interrupted'
        section['value'] = value

    def status(self):
        self.state('manifest', CONFIG_DIR / 'manifest.json')
        self.state('last_setup', STATE_DIR / 'last-setup.json')
        self.state('last_action', STATE_DIR / 'last-action.json')
        self.file('health', Path('/var/lib/bootstrap-health/current-state'))
        self.file('delivered_alert', Path('/var/lib/bootstrap-health/last-alert-state'))
        self.file('uptime', PROC_DIR / 'uptime')
        for resource in ('cpu', 'memory', 'io'):
            self.file('pressure_' + resource, PROC_DIR / 'pressure' / resource)
        self.sections['reboot_required'] = {'status': 'ok', 'value': Path('/var/run/reboot-required').exists()}
        for name, command in {
            'disk': ['df', '-h', '/'], 'inodes': ['df', '-i', '/'], 'memory': ['free', '-m'],
            'clock': ['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
            'failed_units': ['systemctl', '--failed', '--no-pager', '--no-legend'],
            'oom': ['journalctl', '-k', '-b', '--grep', 'Out of memory|Killed process|oom-kill', '-n', '20', '--utc', '--no-pager'],
            'codex': ['codex', '--version'], 'docker': ['docker', '--version'], 'tailscale': ['tailscale', 'version'],
        }.items():
            self.command(name, command)
        for unit in ('docker', 'tailscaled', 'bootstrap-deploy-webhook', 'bootstrap-healthcheck.timer'):
            self.command('service_' + unit, ['systemctl', 'show', unit, '-p', 'ActiveState,SubState,UnitFileState', '--no-pager'])
        # Inspect only named fields: full inspection can expose environment secrets.
        template = '{"image":{{json .Config.Image}},"digest":{{json .Image}},"state":{{json .State.Status}},"exit_code":{{.State.ExitCode}},"oom":{{.State.OOMKilled}},"restarts":{{.RestartCount}},"log_config":{{json .HostConfig.LogConfig}}}'
        for compose in sorted(STACKS_DIR.glob('*/compose.yaml'))[:16]:
            result = self.command('stack_' + compose.parent.name, ['docker', 'compose', '-f', str(compose), 'ps', '-aq'])
            if result['status'] != 'ok':
                continue
            for container in result['output'].splitlines()[:16]:
                if re.fullmatch(r'[a-f0-9]{12,64}', container):
                    self.command('container_' + container[:12], ['docker', 'inspect', '--format', template, container])

    def logs(self, component):
        journal_args = {
            'host': ['-p', 'warning'], 'actions': ['-t', 'bootstrap-action'],
            'health': ['-u', 'bootstrap-healthcheck'],
        }
        if component in journal_args:
            self.command('logs_' + component, ['journalctl', '--since', '-1h', '-n', '200', '--utc', '--no-pager', *journal_args[component]])
        elif component == 'access':
            self.command('logs_access', ['tail', '-n', '200', str(STACKS_DIR / 'proxy/logs/access-retained.log')])
        elif re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', component):
            compose = STACKS_DIR / component / 'compose.yaml'
            if not compose.is_file():
                self.sections['logs_' + component] = {'status': 'unavailable'}
                return
            self.command('logs_' + component, ['docker', 'compose', '-f', str(compose), 'logs', '--no-color', '--timestamps', '--since', '1h', '--tail', '200'])
        else:
            raise ValueError('invalid component name')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['status', 'logs', 'diagnose'])
    parser.add_argument('component', nargs='?')
    args = parser.parse_args()
    if args.operation == 'logs' and not args.component:
        parser.error('logs needs a component: host, actions, health, access, or a stack name')
    report = Report()
    if args.operation != 'logs':
        report.status()
    if args.operation == 'diagnose':
        for component in ('host', 'actions', 'health', 'proxy', 'tunnel'):
            report.logs(component)
    if args.operation == 'logs':
        try:
            report.logs(args.component)
        except ValueError as error:
            parser.error(str(error))
    result = {'at': datetime.now(timezone.utc).isoformat(), 'sections': report.sections,
              'incomplete': any(section['status'] != 'ok' for section in report.sections.values())}
    output = json.dumps(result, ensure_ascii=False)
    if len(output.encode()) > 1024 * 1024:
        output = json.dumps({'incomplete': True, 'reason': 'report size limit exceeded'})
    print(output)


if __name__ == '__main__':
    main()
