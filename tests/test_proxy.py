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
    compose = ["compose", "-p", root.name, "-f", str(root / "compose.yaml")]
    config = json.loads(docker(*compose, "config", "--format", "json"))
    # Use an isolated network; keep the generated service and volume settings.
    config["networks"]["edge"] = {}
    (root / "compose.json").write_text(json.dumps(config))
    compose[-1] = str(root / "compose.json")
    image = config["services"]["caddy"]["image"]
    docker_root = docker("info", "--format", "{{.DockerRootDir}}")

    try:
        docker(*compose, "up", "-d")
        container = docker(*compose, "ps", "-q", "caddy")

        def inspect():
            return json.loads(docker("inspect", container))[0]

        def manually_stopped():
            state_file = f"{docker_root}/containers/{container}/config.v2.json"
            state = docker("run", "--rm", "--network", "none", "-v",
                           f"{state_file}:/state.json:ro", image, "cat", "/state.json")
            return json.loads(state)["HasBeenManuallyStopped"]

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
        assert writer["roll_keep"] == 64
        assert writer["roll_keep_days"] == 7
        before = inspect()
        assert before["HostConfig"]["RestartPolicy"]["Name"] == "always"
        assert not manually_stopped()

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
        assert not manually_stopped()

        raw = docker("exec", container, "cat", "/var/log/caddy/access-retained.log")
        assert marker not in raw
        entries = [json.loads(line) for line in raw.splitlines()]
        assert len(entries) >= 2
        for entry in entries:
            assert entry["request"]["uri"] == "/probe"
            assert entry["request"]["method"] == "GET"
            assert entry["status"] == 200
            assert "duration" in entry
            assert not set(sensitive).intersection(entry["request"]["headers"])
        print("PASS: generated proxy config, log redaction, reload, and restart state")
    finally:
        docker(*compose, "down", "-v", "--remove-orphans")
