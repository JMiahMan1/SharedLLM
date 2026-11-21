"""The launchd agent: an idempotent installer with no secrets in it, and a runner
that syncs every source, flushes the queue and rotates its own log.

launchctl is replaced by a stub that records its calls, HOME is a temporary
directory, and python is a stub, so nothing here touches the real launchd,
sessions or gateway.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "teacher-sync-launchd"
KEY = "synthetic-uploader-key-value"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    key = home / "trainer-key"
    key.write_text(KEY, encoding="utf-8")
    key.chmod(0o600)
    calls = tmp_path / "launchctl.calls"
    stub = tmp_path / "launchctl"
    stub.write_text(f'#!/bin/bash\necho "$*" >> {calls}\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "LAUNCHCTL": str(stub)}
    return {"home": home, "key": key, "calls": calls, "env": env}


def _install(home, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(TOOL / "install.sh"), "--gateway", "https://gateway.test", "--key-file", str(home["key"]),
         "--python", sys.executable, *extra],
        env=home["env"], capture_output=True, text=True, check=False,
    )


def test_install_writes_the_agent_without_secrets_and_loads_it(home):
    result = _install(home)

    assert result.returncode == 0, result.stderr
    plist = home["home"] / "Library" / "LaunchAgents" / "com.sharedllm.teacher-sync.plist"
    config = home["home"] / ".config" / "sharedllm" / "teacher-sync.env"
    text = plist.read_text()
    assert "<integer>1800</integer>" in text and "run.sh" in text and str(config) in text
    assert KEY not in text and KEY not in config.read_text(), "the key stays in its file"
    assert str(home["key"]) in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o600
    assert "bootstrap" in home["calls"].read_text()
    assert "Installed the teacher-trace sync" in result.stdout


def test_install_is_idempotent(home):
    _install(home)
    again = _install(home)

    assert again.returncode == 0, again.stderr
    assert again.stdout.count("unchanged") == 2
    assert home["calls"].read_text().count("bootstrap") == 2, "reloaded, not duplicated"


def test_install_refuses_a_key_file_others_can_read(home):
    home["key"].chmod(0o644)

    result = _install(home)

    assert result.returncode == 2
    assert "chmod 600" in result.stderr


def test_install_needs_a_gateway(home):
    result = subprocess.run(["bash", str(TOOL / "install.sh"), "--key-file", str(home["key"])],
                            env=home["env"], capture_output=True, text=True, check=False)

    assert result.returncode == 2 and "--gateway is required" in result.stderr


def test_uninstall_removes_the_agent(home):
    _install(home)
    result = subprocess.run(["bash", str(TOOL / "uninstall.sh")], env=home["env"], capture_output=True, text=True,
                            check=False)

    assert result.returncode == 0
    assert not (home["home"] / "Library" / "LaunchAgents" / "com.sharedllm.teacher-sync.plist").exists()


def _runner(home, tmp_path, exit_code: int, max_bytes: int = 1_048_576):
    fake = tmp_path / "fake-python"
    fake.write_text(f'#!/bin/bash\necho "ran $2"\nexit {exit_code}\n', encoding="utf-8")
    fake.chmod(0o755)
    logs = tmp_path / "logs"
    config = tmp_path / "teacher-sync.env"
    config.write_text(
        f"TEACHER_REPO='{ROOT}'\nTEACHER_GATEWAY='https://gateway.test'\nTEACHER_KEY_FILE='{home['key']}'\n"
        f"TEACHER_PYTHON='{fake}'\nTEACHER_LOG_DIR='{logs}'\nTEACHER_STATE_DIR='{tmp_path / 'state'}'\n"
        f"TEACHER_MAX_LOG_BYTES='{max_bytes}'\nTEACHER_KEEP_LOGS='2'\n",
        encoding="utf-8",
    )
    return config, logs


def _run(home, config) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TOOL / "run.sh"), str(config)], env=home["env"], capture_output=True,
                          text=True, check=False)


def test_the_runner_syncs_then_flushes_and_logs(home, tmp_path):
    config, logs = _runner(home, tmp_path, 0)

    assert _run(home, config).returncode == 0

    log = (logs / "sync.log").read_text()
    assert "ran sync" in log and "ran flush" in log and "sync exit 0" in log
    assert KEY not in log


def test_queued_work_is_not_a_launchd_failure_but_errors_are(home, tmp_path):
    config, _ = _runner(home, tmp_path, 3)
    assert _run(home, config).returncode == 0

    failing = tmp_path / "failing"
    failing.mkdir()
    config, _ = _runner(home, failing, 2)
    assert _run(home, config).returncode == 2


def test_the_log_rotates_and_keeps_a_bounded_number(home, tmp_path):
    config, logs = _runner(home, tmp_path, 0, max_bytes=10)
    for _ in range(5):
        _run(home, config)

    names = sorted(p.name for p in logs.glob("sync.log*"))
    assert names == ["sync.log", "sync.log.1", "sync.log.2"]
