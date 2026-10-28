#!/usr/bin/env python3
"""Test the proxy templates in disposable containers on a local Docker engine."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
if not os.environ.get("DOCKER_CONTEXT"):
    endpoint = os.environ.get("DOCKER_HOST", endpoint)
assert endpoint.startswith("unix://"), "Select a local Docker context."

source = (Path(__file__).resolve().parents[1] / "bootstrap/remote/init-host.sh").read_text()


def template(name):
    marker = f'write_if_changed "/srv/stacks/proxy/{name}" <<\'EOF\'\n'
    return source.split(marker, 1)[1].split("\nEOF", 1)[0] + "\n"


with tempfile.TemporaryDirectory(prefix="bootstrap-proxy-") as directory:
    root = Path(directory)
    (root / "Caddyfile").write_text(template("Caddyfile"))
    (root / "compose.yaml").write_text(template("compose.yaml"))
    (root / "sites").mkdir()
    (root / "logs").mkdir()
    route = root / "sites/test.caddy"
    route.write_text('handle /probe {\n  respond "before {http.request.uri}"\n}\n')
    (root / "sites/error.caddy").write_text('handle /unreachable {\n  reverse_proxy 127.0.0.1:1\n}\n')
    compose = ["compose", "-p", root.name, "-f", str(root / "compose.yaml")]
    config = json.loads(docker(*compose, "config", "--format", "json"))
    # Use an isolated network; keep the generated service and volume settings.
    config["networks"]["edge"] = {}
    (root / "compose.json").write_text(json.dumps(config))
    compose[-1] = str(root / "compose.json")
    image = config["services"]["caddy"]["image"]

    try:
        docker(*compose, "up", "-d")
        container = docker(*compose, "ps", "-q", "caddy")

        def inspect():
            return json.loads(docker("inspect", container))[0]


        sensitive = ["Authorization", "Proxy-Authorization", "X-Deploy-Token",
                     "Cookie", "Set-Cookie", "Referer"]
        marker = "private-test-value"
        target = f"/probe?token={marker}&q={marker}"
        request = ["exec", container, "wget", "-qO-"]
        for header in sensitive:
            request += ["--header", f"{header}: {marker}"]
        request += ["http://localhost" + target]

        def expect_response(prefix):
            for _ in range(50):
                result = subprocess.run(["docker", *request], text=True, capture_output=True)
                if result.returncode == 0 and result.stdout == prefix + " " + target:
                    return
                time.sleep(0.1)
            raise AssertionError(f"Expected {prefix!r}; got {result.stdout!r}: {result.stderr}")

        expect_response("before")
        adapted = json.loads(docker("exec", container, "caddy", "adapt", "--config",
                                    "/etc/caddy/Caddyfile", "--adapter", "caddyfile"))
        writer = adapted["logging"]["logs"]["log0"]["writer"]
        assert writer["roll_size_mb"] == 20
        assert writer["roll_keep"] == 8
        assert writer["roll_keep_days"] == 7
        assert docker("exec", container, "caddy", "version").startswith("v2.11.7")
        before = inspect()
        assert before["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"

        failed = subprocess.run(["docker", *request[:-1], f"http://localhost/unreachable?token={marker}"],
                                capture_output=True, text=True)
        assert failed.returncode != 0
        runtime = subprocess.run(["docker", "logs", container], capture_output=True, text=True, check=True)
        runtime = runtime.stdout + runtime.stderr
        assert marker not in runtime, 'Runtime logs exposed a request secret'
        errors = [json.loads(line) for line in runtime.splitlines() if line.startswith('{')]
        errors = [entry for entry in errors if entry.get('logger', '').startswith('http.log.error')]
        assert errors and any(entry.get('status') == 502 for entry in errors)
        assert all('request' not in entry for entry in errors)
        assert any('127.0.0.1:1' in entry['msg'] for entry in errors), 'The upstream error cause was lost'
        access = docker("exec", container, "cat", "/var/log/caddy/access-retained.log")
        assert marker not in access
        assert any(entry['status'] == 502 and entry['request']['uri'] == '/unreachable'
                   for entry in map(json.loads, access.splitlines()))

        route.write_text('handle /probe {\n  respond "after {http.request.uri}"\n}\n')
        docker(*compose, "exec", "-T", "caddy", "caddy", "validate", "--config",
               "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
        docker(*compose, "exec", "-T", "caddy", "sh", "-c", "kill -USR1 1")
        expect_response("after")
        after = inspect()
        assert after["State"]["Running"]
        assert after["State"]["Pid"] == before["State"]["Pid"]
        assert after["State"]["StartedAt"] == before["State"]["StartedAt"]
        assert after["RestartCount"] == before["RestartCount"] == 0

        failed = subprocess.run(["docker", "exec", container, "wget", "-qO-",
                                 "--header", f"Authorization: {marker}", f"http://localhost/missing?secret={marker}"],
                                capture_output=True, text=True)
        assert failed.returncode != 0
        docker(*compose, "up", "-d", "--force-recreate")
        container = docker(*compose, "ps", "-q", "caddy")
        request[1] = container
        expect_response("after")
        raw = docker("exec", container, "cat", "/var/log/caddy/access-retained.log")
        assert marker not in raw
        entries = [json.loads(line) for line in raw.splitlines()]
        assert len(entries) >= 2
        for entry in entries:
            assert entry["request"]["uri"] in ("/probe", "/missing", "/unreachable")
            assert entry["request"]["method"] == "GET"
            assert entry["status"] in (200, 404, 502)
            assert "duration" in entry
            assert not set(sensitive).intersection(entry["request"]["headers"])
        print("PASS: generated proxy config, log redaction, reload, and restart state")
    finally:
        docker(*compose, "down", "-v", "--remove-orphans")
