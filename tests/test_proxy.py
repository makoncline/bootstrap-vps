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
    config["services"]["caddy"]["ports"] = ["127.0.0.1::80"]
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

        origin = "http://" + docker(*compose, "port", "caddy", "80")
        candidate = root / "candidate.caddy"
        candidate.write_text('handle /probe {\n  respond "after {http.request.uri}"\n}\n')
        apply = ["python3", str(Path(__file__).resolve().parents[1] / "bootstrap/remote/apply-proxy.py"),
                 "--compose", str(root / "compose.json"), "--smoke-url", origin + "/probe", str(candidate), str(route)]
        subprocess.run(apply, check=True)
        previous = route.read_text()
        candidate.write_text('this-is-not-a-caddy-directive\n')
        assert subprocess.run(apply).returncode == 1
        assert route.read_text() == previous
        expect_response("after")
        candidate.write_text('handle /probe {\n  respond "unhealthy" 500\n}\n')
        assert subprocess.run(apply).returncode == 1
        assert route.read_text() == previous
        expect_response("after")
        candidate.write_text(previous)
        subprocess.run(apply, check=True)

        # Run config sync against a local Git fixture and the real proxy.
        repo = root / "repo"
        files = repo / "deploy/vps"
        files.mkdir(parents=True)
        app_compose = 'services:\n  example:\n    image: caddy:2.11.7\n'
        (files / "compose.yaml").write_text(app_compose)
        (files / "caddy-route.caddy").write_text(previous)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"], check=True)
        app = root / "app"
        app.mkdir()
        (app / "compose.yaml").write_text(app_compose)
        (app / ".env").write_text("")
        targets = root / "targets"
        targets.mkdir()
        (targets / "example.env").write_text(f"REPO_URL={repo}\nSTACK_DIR={app}\nROUTE_DEST={route}\nCHECKOUT_DIR={root}/checkout\nSMOKE_URL={origin}/probe\n")
        tools = root / "bin"
        tools.mkdir()
        helper = tools / "apply"
        helper.write_text((Path(__file__).resolve().parents[1] / "bootstrap/remote/apply-proxy.py").read_text().replace("/srv/stacks/proxy/compose.yaml", str(root / "compose.json")))
        helper.chmod(0o755)
        (tools / "flock").write_text("#!/bin/sh\nexit 0\n")
        (tools / "flock").chmod(0o755)
        (tools / "notify").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{root}/notifications"\n')
        (tools / "notify").chmod(0o755)
        sync = source.split('write_if_changed "/usr/local/bin/bootstrap-sync-config.sh" <<\'EOF\'\n', 1)[1].split("\nEOF", 1)[0]
        sync = sync.replace("/etc/bootstrap/config-sync", str(targets)).replace("/usr/local/bin/bootstrap-apply-proxy", str(helper)).replace("/usr/local/bin/notify", str(tools / "notify"))
        (root / "sync.sh").write_text(sync)
        fixture_env = dict(os.environ, HOME=str(root), DOCKER_CONFIG=os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")), PATH=f"{tools}:{os.environ['PATH']}")
        subprocess.run(["bash", str(root / "sync.sh"), "example", "HEAD"], check=True, env=fixture_env)
        assert "config sync successful" in (root / "notifications").read_text()
        expect_response("after")
        (files / "caddy-route.caddy").write_text("invalid-directive\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "invalid route"], check=True)
        (root / "notifications").write_text("")
        assert subprocess.run(["bash", str(root / "sync.sh"), "example", "HEAD"], env=fixture_env).returncode == 1
        assert "successful" not in (root / "notifications").read_text()
        assert route.read_text() == previous
        expect_response("after")

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
            assert entry["status"] in (200, 404, 500, 502)
            assert "duration" in entry
            assert not set(sensitive).intersection(entry["request"]["headers"])
        print("PASS: generated proxy config, log redaction, reload, and restart state")
    finally:
        docker(*compose, "down", "-v", "--remove-orphans")
