"""The family PC installer is a parent's download, nobody else's.

scripts/deploy_remote.sh copies the latest green Windows build (exe, its
SHA-256, install.ps1, uninstall.ps1, README and a zip of them) into
data/app_updates/windows/. The gateway serves those names only, and only to
someone identity says may manage the family's devices.
"""
import json

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def installer(tmp_path, monkeypatch):
    (tmp_path / "jarvis-guard.exe").write_bytes(b"MZ" + b"\0" * 64)
    (tmp_path / "install.ps1").write_text("# installer\n")
    (tmp_path / "version.json").write_text(json.dumps({
        "version": "0.2.0", "exe_sha256": "ab" * 32, "git_sha": "0123abcd", "built_at": "2026-01-05T10:00:00Z",
    }))
    (tmp_path / "secret.txt").write_text("not served")
    monkeypatch.setattr(gateway_main, "WINDOWS_INSTALLER_DIR", tmp_path)
    return tmp_path


def _manager(monkeypatch, status=200, can_manage=True):
    asked = []

    async def _as_caller(request, method, path, **kw):
        asked.append((method, path, request.headers.get("Authorization")))
        return status, {"can_manage": can_manage}

    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    return asked


def _client():
    return TestClient(app, headers={"Authorization": "Bearer parent-key"})


def test_a_parent_sees_the_version_and_hash(installer, monkeypatch):
    asked = _manager(monkeypatch)
    body = _client().get("/api/app-updates/windows/info").json()
    assert body["available"] is True
    assert (body["version"], body["sha256"]) == ("0.2.0", "ab" * 32)
    assert {f["name"] for f in body["files"]} == {"jarvis-guard.exe", "install.ps1"}
    assert asked == [("GET", "/api/user-panel/device-manager", "Bearer parent-key")]


def test_a_parent_downloads_a_file(installer, monkeypatch):
    _manager(monkeypatch)
    r = _client().get("/api/app-updates/windows/files/jarvis-guard.exe")
    assert r.status_code == 200 and r.content.startswith(b"MZ")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"


def test_a_child_gets_nothing(installer, monkeypatch):
    _manager(monkeypatch, can_manage=False)
    assert _client().get("/api/app-updates/windows/info").status_code == 403
    assert _client().get("/api/app-updates/windows/files/jarvis-guard.exe").status_code == 403


def test_nobody_signed_in_gets_nothing(installer, monkeypatch):
    asked = _manager(monkeypatch)
    assert TestClient(app).get("/api/app-updates/windows/files/install.ps1").status_code == 401
    assert asked == []


def test_only_the_installers_own_files_are_served(installer, monkeypatch):
    _manager(monkeypatch)
    assert _client().get("/api/app-updates/windows/files/secret.txt").status_code == 404
    assert _client().get("/api/app-updates/windows/files/version.json").status_code == 404
    assert _client().get("/api/app-updates/windows/files/uninstall.ps1").status_code == 404  # not copied here


def test_nothing_deployed_yet_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_main, "WINDOWS_INSTALLER_DIR", tmp_path / "missing")
    _manager(monkeypatch)
    body = _client().get("/api/app-updates/windows/info").json()
    assert body == {"available": False, "version": None, "sha256": None, "git_sha": None, "built_at": None, "files": []}
