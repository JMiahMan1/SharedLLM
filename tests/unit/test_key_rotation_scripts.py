"""The operator scripts for a key leak: no key is ever printed, and each fails fast.

scripts/fernet_env.py edits FERNET_KEY in a .env; scripts/create_test_account.py writes the
test account's key to a mode-600 file; scripts/rotate_fernet_key.sh and
scripts/reset_sign_ins.sh drive the rotation and the sign-in reset on the server. Only
made-up values are used.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fernet_env = _load("fernet_env.py")
create_test_account = _load("create_test_account.py")


@pytest.fixture
def env_file(tmp_path):
    key = Fernet.generate_key().decode()
    path = tmp_path / ".env"
    path.write_text(f"# a comment\nINTERNAL_SECRET=made-up-internal\nFERNET_KEY={key}\nTZ=UTC\n")
    os.chmod(path, 0o600)
    return path, key


def test_stage_new_moves_the_key_and_keeps_everything_else(env_file, capsys):
    path, old = env_file
    assert fernet_env.main(["stage-new", "--env", str(path)]) == 0
    out = capsys.readouterr().out
    text = path.read_text()
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    assert values["FERNET_KEY_PREVIOUS"] == old
    new = values["FERNET_KEY"]
    assert new != old and len(base64.urlsafe_b64decode(new)) == 32
    Fernet(new.encode())  # a real Fernet key
    assert values["INTERNAL_SECRET"] == "made-up-internal" and values["TZ"] == "UTC"
    assert text.startswith("# a comment\n")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # Only fingerprints are printed, never a key.
    assert old not in out and new not in out
    status = json.loads(out)
    assert status["rotating"] is True and status["previous"] == [fernet_env.fingerprint(old)]


def test_stage_new_refuses_while_a_rotation_is_in_progress(env_file):
    path, _ = env_file
    fernet_env.main(["stage-new", "--env", str(path)])
    before = path.read_text()
    with pytest.raises(SystemExit) as exc:
        fernet_env.main(["stage-new", "--env", str(path)])
    assert "in progress" in str(exc.value)
    assert path.read_text() == before


def test_stage_new_refuses_a_missing_or_bad_key(tmp_path):
    path = tmp_path / ".env"
    path.write_text("FERNET_KEY=not-a-fernet-key\n")
    with pytest.raises(SystemExit) as exc:
        fernet_env.main(["stage-new", "--env", str(path)])
    assert "not a valid Fernet key" in str(exc.value) and "not-a-fernet-key" not in str(exc.value)
    path.write_text("TZ=UTC\n")
    with pytest.raises(SystemExit):
        fernet_env.main(["stage-new", "--env", str(path)])


def test_drop_previous_removes_only_that_line(env_file, capsys):
    path, _ = env_file
    fernet_env.main(["stage-new", "--env", str(path)])
    new = next(line for line in path.read_text().splitlines() if line.startswith("FERNET_KEY="))
    fernet_env.main(["drop-previous", "--env", str(path)])
    text = path.read_text()
    assert "FERNET_KEY_PREVIOUS" not in text and new in text
    capsys.readouterr()
    fernet_env.main(["status", "--env", str(path)])
    assert json.loads(capsys.readouterr().out)["rotating"] is False


def test_status_warns_when_others_can_read_the_env(env_file, capsys):
    path, key = env_file
    os.chmod(path, 0o644)
    fernet_env.main(["status", "--env", str(path)])
    out = capsys.readouterr().out
    assert json.loads(out)["readable_by_others"] is True and key not in out


def test_missing_env_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        fernet_env.main(["status", "--env", str(tmp_path / "nope.env")])


# -- the test account's key file ---------------------------------------------------------

def test_write_secret_is_mode_600_in_a_700_directory(tmp_path):
    path = create_test_account.write_secret(str(tmp_path / "jarvis" / "agent.key"), "fake-test-key")
    assert Path(path).read_text() == "fake-test-key\n"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


def test_create_test_account_never_prints_the_key(tmp_path, monkeypatch, capsys):
    admin_file = tmp_path / "admin.key"
    admin_file.write_text("fake-admin-key\n")
    os.chmod(admin_file, 0o600)
    calls = []

    def fake_post(url, body, bearer=None):
        calls.append((url, body, bearer is not None))
        return {"status": "SUCCESS", "username": "test-agent", "created": True, "api_key": "fake-agent-key-xyz"}

    monkeypatch.setattr(create_test_account, "_post", fake_post)
    out_path = tmp_path / "cfg" / "test-agent.key"
    status = create_test_account.main(["--server", "https://jarvis.invalid", "--admin-key-file", str(admin_file),
                                       "--out", str(out_path)])
    assert status == 0
    printed = capsys.readouterr()
    assert "fake-agent-key-xyz" not in printed.out + printed.err
    assert "fake-admin-key" not in printed.out + printed.err
    assert out_path.read_text().strip() == "fake-agent-key-xyz"
    assert calls == [("https://jarvis.invalid/api/users/security/test-account",
                      {"username": "test-agent", "display_name": "Test agent"}, True)]


def test_create_test_account_refuses_plain_http_and_a_loose_admin_key_file(tmp_path, capsys):
    assert create_test_account.main(["--server", "http://jarvis.invalid"]) == 1
    loose = tmp_path / "admin.key"
    loose.write_text("fake-admin-key\n")
    os.chmod(loose, 0o644)
    assert create_test_account.main(["--server", "https://jarvis.invalid", "--admin-key-file", str(loose)]) == 1
    assert "chmod 600" in capsys.readouterr().err


def test_create_test_account_needs_a_server(monkeypatch, capsys):
    monkeypatch.delenv("JARVIS_SERVER_URL", raising=False)
    assert create_test_account.main([]) == 1
    assert "--server" in capsys.readouterr().err


# -- the shell scripts -----------------------------------------------------------------

@pytest.mark.parametrize("name", ["rotate_fernet_key.sh", "reset_sign_ins.sh", "fernet_env.py",
                                  "create_test_account.py"])
def test_scripts_are_executable(name):
    assert os.access(SCRIPTS / name, os.X_OK), f"{name} lost its executable bit"


@pytest.mark.parametrize("name", ["rotate_fernet_key.sh", "reset_sign_ins.sh"])
def test_shell_scripts_parse_and_show_usage(name):
    assert subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=False).returncode == 0
    usage = subprocess.run(["bash", str(SCRIPTS / name)], capture_output=True, text=True, check=False)
    assert usage.returncode == 2 and usage.stdout.strip()


def test_rotation_script_never_prints_the_env():
    text = (SCRIPTS / "rotate_fernet_key.sh").read_text()
    for risky in ("cat \"$ENV_FILE\"", "cat $ENV_FILE", "echo \"$FERNET_KEY", "set -x", "--passphrase \""):
        assert risky not in text
    assert "--passphrase-fd 3" in text  # the backup passphrase goes through a pipe, never an argument


def test_rotation_script_refuses_without_an_env(tmp_path):
    result = subprocess.run(["bash", str(SCRIPTS / "rotate_fernet_key.sh"), "preflight"], capture_output=True,
                            text=True, check=False, env={**os.environ, "ENV_FILE": str(tmp_path / "missing.env"),
                                                         "COMPOSE": "true"})
    assert result.returncode != 0
    assert "does not exist" in result.stderr or "needed" in result.stderr
