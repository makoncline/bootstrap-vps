#!/usr/bin/env python3
"""Check admin access and lockdown in disposable Linux network namespaces."""
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], text=True, **kwargs).strip()


endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
if not os.environ.get("DOCKER_CONTEXT"):
    endpoint = os.environ.get("DOCKER_HOST", endpoint)
assert endpoint.startswith("unix://"), "Select a local Docker engine."
image = "bootstrap-ssh-test:local"
docker("build", "-q", "-t", image, "-", input="""FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server openssh-client sudo ufw iproute2 jq && rm -rf /var/lib/apt/lists/*
CMD ["sleep", "infinity"]
""")
prefix = "bootstrap-ssh-" + uuid.uuid4().hex[:10]
public = prefix + "-public"
private = prefix + "-private"
server = prefix + "-server"
client = prefix + "-client"
try:
    docker("network", "create", public)
    docker("network", "create", private)
    docker("run", "-d", "--name", server, "--cap-add=NET_ADMIN", "--network", public, image)
    docker("network", "connect", private, server)
    docker("run", "-d", "--name", client, "--network", public, image)
    docker("network", "connect", private, client)
    networks = json.loads(docker("inspect", server))[0]["NetworkSettings"]["Networks"]
    public_ip = networks[public]["IPAddress"]
    private_ip = networks[private]["IPAddress"]
    docker("cp", str(ROOT / "bootstrap"), server + ":/tmp/bootstrap")
    docker("exec", client, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "/tmp/key")
    docker("exec", client, "mkdir", "-p", "/root/.ssh")
    key = docker("exec", client, "cat", "/tmp/key.pub")
    docker("exec", server, "bash", "-c", '''
set -eu
source /tmp/bootstrap/lib/common.sh
source <(sed -n '/^ensure_admin_user() {/,/^}/p' /tmp/bootstrap/remote/init-host.sh)
source <(sed -n '/^ensure_ssh_base_config() {/,/^}/p' /tmp/bootstrap/remote/init-host.sh)
restart_ssh_service() { :; }
ADMIN_USER=admin
ADMIN_SSH_PUBKEY=$1
ensure_admin_user
install -d -m 700 /root/.ssh
cp /home/admin/.ssh/authorized_keys /root/.ssh/authorized_keys
mkdir -p /run/sshd
ensure_ssh_base_config
/usr/sbin/sshd
printf 'ADMIN_USER=admin\\n' > /tmp/host.env
cat > /usr/local/bin/systemctl <<'SH'
#!/bin/sh
kill -HUP "$(cat /run/sshd.pid)"
SH
chmod +x /usr/local/bin/systemctl
interface=$(ip -o -4 addr show | awk -v address="$2" '$4 ~ ("^" address "/") {print $2}')
ip link set "$interface" down
ip link set "$interface" name tailscale0
ip link set tailscale0 up
''', "test", key, private_ip)
    host_key = docker("exec", server, "cat", "/etc/ssh/ssh_host_ed25519_key.pub")
    for address in (public_ip, private_ip):
        docker("exec", client, "sh", "-c", 'printf "%s %s\\n" "$1" "$2" >> /root/.ssh/known_hosts', "test", address, host_key)

    def ssh(address, command="sudo -n true", user="admin", success=True):
        result = subprocess.run(["docker", "exec", client, "ssh", "-F", "/dev/null", "-i", "/tmp/key",
                                 "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                                 "-o", "ConnectTimeout=3", f"{user}@{address}", command], capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stderr

    ssh(private_ip)
    ssh(private_ip, user="root")
    client_ip = json.loads(docker("inspect", client))[0]["NetworkSettings"]["Networks"][private]["IPAddress"]
    docker("exec", server, "bash", "-c", '''
set -eu
source /tmp/bootstrap/lib/common.sh
source <(sed -n '/^ensure_admin_user() {/,/^}/p' /tmp/bootstrap/remote/init-host.sh)
ADMIN_USER=admin
ADMIN_SSH_PUBKEY=$1
printf 'from="%s",no-port-forwarding %s\\n' "$2" "$1" > /home/admin/.ssh/authorized_keys
cp /home/admin/.ssh/authorized_keys /tmp/expected-keys
ensure_admin_user
cmp /tmp/expected-keys /home/admin/.ssh/authorized_keys
''', "test", key, client_ip)
    ssh(private_ip)
    docker("exec", server, "bash", "/tmp/bootstrap/remote/lockdown-ssh.sh", "/tmp/host.env")
    ssh(private_ip)
    ssh(private_ip, user="root", success=False)
    ssh(public_ip, success=False)
    public_probe = subprocess.run(["docker", "exec", client, "timeout", "3", "bash", "-c",
                                   'exec 3<>/dev/tcp/$1/22', "test", public_ip], capture_output=True)
    assert public_probe.returncode != 0, "Public TCP port 22 is still open"
    # A stored host key mismatch must fail even with first-use acceptance enabled.
    docker("exec", client, "sh", "-c", 'ssh-keygen -q -t ed25519 -N "" -f /tmp/wrong; cut -d" " -f1,2 /tmp/wrong.pub > /tmp/wrong.pubkey')
    wrong = docker("exec", client, "cat", "/tmp/wrong.pubkey")
    docker("exec", client, "sh", "-c", 'printf "%s %s\\n" "$1" "$2" > /root/.ssh/known_hosts', "test", private_ip, wrong)
    ssh(private_ip, success=False)
    print("PASS: fresh key login and sudo, root denial, private-interface access, public denial, host-key rejection")
finally:
    subprocess.run(["docker", "rm", "-f", server, client], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for network in (public, private):
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
