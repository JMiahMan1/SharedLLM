"""The deploy tells the app it is updating, and always says when it is done.

scripts/deploy_remote.sh writes data/deploy_status/status.json on the server;
Caddy serves it at /api/deploy-status and the app shows "Jarvis is updating"
while it says so. A flag left on would show the banner forever, so it must be
cleared on every way out of the remote session, including a failure and a
rollback (the app also ignores a flag older than 15 minutes).
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts" / "deploy_remote.sh").read_text()


def _remote_function() -> str:
    """The deploy_status function as the remote shell receives it (the heredoc is unquoted)."""
    m = re.search(r"^    deploy_status\(\) \{\n.*?^    \}\n", SCRIPT, re.S | re.M)
    assert m, "deploy_status() not found in deploy_remote.sh"
    return m.group(0).replace("\\$", "$").replace("\\\n", "")


def test_the_flag_goes_up_before_the_recreate_and_is_cleared_on_any_exit():
    up = SCRIPT.index("deploy_status true")
    trap = SCRIPT.index("trap 'deploy_status false")
    recreate = SCRIPT.index("docker compose up -d --force-recreate $SERVICES\n\n")
    assert up < trap < recreate
    # the first rollback path comes after the trap, so it is covered too
    assert trap < SCRIPT.index("roll_back\n            exit 1")


def test_the_status_file_is_json_the_app_can_read(tmp_path):
    (tmp_path / "data" / "deploy_status").mkdir(parents=True, exist_ok=True)
    subprocess.run(["bash", "-c", _remote_function() + "deploy_status true false\n"], cwd=tmp_path, check=True)
    status = json.loads((tmp_path / "data" / "deploy_status" / "status.json").read_text())
    assert status["updating"] is True and status["app"] is False and isinstance(status["at"], int)
    assert not (tmp_path / "data" / "deploy_status" / ".status.json.tmp").exists()


def test_caddy_serves_it_without_a_service_and_never_caches_it():
    caddy = (ROOT / "Caddyfile").read_text()
    block = re.search(r"handle /api/deploy-status \{\n(.*?)\n    \}", caddy, re.S)
    assert block, "Caddyfile has no /api/deploy-status route"
    body = block.group(1)
    assert "file_server" in body and "reverse_proxy" not in body
    assert "no-store" in body
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "./data/deploy_status:/srv/deploy_status:ro" in compose
