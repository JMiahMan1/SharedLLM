"""Stopping a published port is the other half of exposing one.

A preview server is stopped and restarted all day; before this route existed the
only way to close a forward was to recreate the container. These tests pin the
contract: the workspace is resolved first (the same access rules as exposing), a
port that is not open reports ``removed: false`` rather than failing, and the
sandbox helper is the thing that actually closes the listener.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _internal_secret(monkeypatch):
    """Set the secret per test, never at import time.

    A module-scope assignment races every config module that reads the same
    variable (tests/unit/test_config_env_isolation.py exists to catch it), and
    the value has to be in place before workspace_runtime.main is imported.
    """
    monkeypatch.setenv("INTERNAL_SECRET", "test-internal-secret")
    return "test-internal-secret"


def _client() -> TestClient:
    from services.workspace_runtime.main import app

    return TestClient(app, headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]})


def test_unexposing_a_port_resolves_the_workspace_then_closes_it(monkeypatch):
    import services.workspace_runtime.main as main
    import services.workspace_sandbox as sandbox

    resolved: list[dict] = []
    closed: list[tuple[str, int]] = []

    monkeypatch.setattr(main, "_resolve_workspace", lambda req, check_recovery=False: resolved.append(req) or {"id": "sermons"})
    monkeypatch.setattr(sandbox, "unexpose_workspace_port", lambda workspace_id, host_port: closed.append((workspace_id, host_port)) or True)

    resp = _client().post("/ports/unexpose", json={"workspace_id": "sermons", "host_port": 14173})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "SUCCESS", "removed": True, "host_port": 14173}
    assert closed == [("sermons", 14173)]
    assert resolved and resolved[0].host_port == 14173


def test_a_port_that_is_not_open_is_reported_not_raised(monkeypatch):
    import services.workspace_runtime.main as main
    import services.workspace_sandbox as sandbox

    monkeypatch.setattr(main, "_resolve_workspace", lambda req, check_recovery=False: {"id": "sermons"})
    monkeypatch.setattr(sandbox, "unexpose_workspace_port", lambda workspace_id, host_port: False)

    resp = _client().post("/ports/unexpose", json={"workspace_id": "sermons", "host_port": 9999})

    assert resp.status_code == 200
    assert resp.json()["removed"] is False


def test_unexposing_needs_the_internal_secret():
    from services.workspace_runtime.main import app

    resp = TestClient(app).post("/ports/unexpose", json={"workspace_id": "sermons", "host_port": 1})

    assert resp.status_code in (401, 403, 422)
