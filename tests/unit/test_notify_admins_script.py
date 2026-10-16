"""scripts/notify_admins.sh sends one admin notice through telemetry, and
deploy_remote.sh uses it to say how a deploy ended.

Both run against fakes on PATH: `docker` runs the script's Python the way the
telemetry container would (its own INTERNAL_SECRET in the environment) against
a local HTTP server, and `ssh` records what the deploy would run remotely.
"""
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "notify_admins.sh"
DEPLOY = ROOT / "scripts" / "deploy_remote.sh"
SECRET = "container-only-secret"


@pytest.fixture
def telemetry():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"path": self.path, "secret": self.headers.get("X-Internal-Secret"),
                             "json": json.loads(body)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "SUCCESS"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], received
    server.shutdown()


def _fake_docker(bin_dir: Path, port: int, log: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$@" > "{log}"\n'
        "shift 5  # compose exec -T telemetry python\n"
        f'exec env INTERNAL_SECRET={SECRET} TELEMETRY_PORT={port} python3 "$@"\n'
    )
    docker.chmod(0o755)


def test_sends_an_admin_notice_without_the_secret_in_args_or_output(tmp_path, telemetry):
    port, received = telemetry
    log = tmp_path / "docker-args"
    _fake_docker(tmp_path, port, log)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    out = subprocess.run(["bash", str(SCRIPT), "Backup failed", "nightly tar: disk full", "4", "admin:backup"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert received[0]["path"] == "/api/telemetry/notify"
    assert received[0]["secret"] == SECRET
    notice = received[0]["json"]
    assert notice["user"] == "@admins" and notice["category"] == "admin"
    assert (notice["title"], notice["body"], notice["priority"], notice["tag"]) == \
        ("Backup failed", "nightly tar: disk full", 4, "admin:backup")
    args = log.read_text()
    assert args.startswith("compose\nexec\n-T\ntelemetry\npython\n")
    assert SECRET not in args and SECRET not in out.stdout + out.stderr


def test_priority_and_tag_are_optional(tmp_path, telemetry):
    port, received = telemetry
    _fake_docker(tmp_path, port, tmp_path / "args")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(SCRIPT), "Cron", "done"], cwd=tmp_path, env=env, check=True,
                   capture_output=True, timeout=30)
    assert received[0]["json"]["priority"] == 3 and received[0]["json"]["tag"] == "admin:script"


def test_usage_error_without_a_title():
    out = subprocess.run(["bash", str(SCRIPT), "only-title"], capture_output=True, text=True, timeout=10)
    assert out.returncode == 2 and "Usage" in out.stderr


def _deploy_notifier() -> str:
    src = DEPLOY.read_text()
    m = re.search(r"^notify_deploy_result\(\) \{\n.*?^\}\n", src, re.S | re.M)
    assert m, "deploy_remote.sh must define notify_deploy_result"
    assert "trap notify_deploy_result EXIT" in src
    return m.group(0)


def _run_deploy_end(tmp_path: Path, exit_code: int, remote_log: str) -> tuple[int, list[str], str]:
    ssh = tmp_path / "ssh"
    ssh.write_text(f'#!/bin/bash\nprintf "%s" "${{@: -1}}" > "{tmp_path}/remote-cmd"\ncat > "{tmp_path}/remote-stdin"\n')
    ssh.chmod(0o755)
    log = tmp_path / "remote.log"
    log.write_text(remote_log)
    harness = (
        "set -euo pipefail\n"
        f'SSH_OPTS="-o BatchMode=yes"; HOST=jeremiah@server; DIR=/srv/jarvis; REMOTE_LOG="{log}"\n'
        'SERVICES="gateway logging"; BRANCH=microservices\n'
        'DEPLOY_STEP="updating the server (pull, restart, health check, OTA bundle)"\n'
        + _deploy_notifier()
        + f"trap notify_deploy_result EXIT\nexit {exit_code}\n"
    )
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    out = subprocess.run(["bash", "-c", harness], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    cmd = (tmp_path / "remote-cmd").read_text()
    assert cmd.startswith("cd '/srv/jarvis' && bash -s -- ")
    args = subprocess.run(["bash", "-c", 'eval "set -- $1"; printf "%s\\0" "$@"', "_",
                           cmd.split("bash -s -- ", 1)[1]], capture_output=True, text=True, check=True).stdout
    assert (tmp_path / "remote-stdin").read_text() == SCRIPT.read_text()
    return out.returncode, args.split("\0")[:-1], out.stdout


def test_failed_deploy_names_the_step_and_the_servers_reason(tmp_path):
    code, args, _ = _run_deploy_end(tmp_path, 1, "Server now at commit: abc1234\n"
                                                 "[FAIL] Timeout waiting for application startup.\n")
    assert code == 1
    title, body, priority, tag = args
    assert (title, priority, tag) == ("Deploy failed", "4", "admin:deploy")
    assert "updating the server" in body and "abc1234" in body
    assert body.endswith("Timeout waiting for application startup.")


def test_finished_deploy_names_the_commit_and_services(tmp_path):
    code, args, _ = _run_deploy_end(tmp_path, 0, "Server now at commit: abc1234\n")
    assert code == 0
    title, body, priority, tag = args
    assert (title, priority, tag) == ("Deploy finished", "2", "admin:deploy")
    assert body.startswith("abc1234") and "microservices" in body and "gateway logging" in body
