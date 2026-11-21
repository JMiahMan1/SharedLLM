"""``scripts/train_jarvis.py`` must authenticate the way the gateway accepts.

The gateway honours ``X-Internal-Secret`` only together with ``X-User-Id`` (the
user an internal caller acts as, ``_acting_identity``). The script used to send
the secret alone, so every call was a 401. These tests run the real script's
``main()`` against the real gateway app (FastAPI's TestClient stands in for the
network), so the headers, the route and the admin gate are all the real ones.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.trainer import runner

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "train_jarvis.py"


class _NoLifespanClient(TestClient):
    """The script uses ``with httpx.Client(...)``; a TestClient used that way would
    start and stop the whole gateway (background workers included) per call. The
    requests still go through every route, middleware and auth check."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _load_script(monkeypatch, admin_user: str | None):
    if admin_user is None:
        monkeypatch.delenv("TRAINER_ADMIN_USER", raising=False)
    else:
        monkeypatch.setenv("TRAINER_ADMIN_USER", admin_user)
    monkeypatch.setenv("INTERNAL_SECRET", gateway_main.INTERNAL_SECRET)
    spec = importlib.util.spec_from_file_location("train_jarvis_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def client(timeout=None):
        # TestClient is an httpx.Client: the script's own request code runs unchanged.
        return _NoLifespanClient(gateway_main.app)

    monkeypatch.setattr(module.httpx, "Client", client)
    return module


def _run(module, monkeypatch, capsys, *argv: str) -> dict:
    monkeypatch.setattr(sys, "argv", ["train_jarvis.py", "--gateway", "http://testserver", *argv])
    module.main()
    return json.loads(capsys.readouterr().out)


def test_the_script_reads_the_catalogue_through_the_real_route(fake_identity, monkeypatch, capsys):
    module = _load_script(monkeypatch, "household-admin")

    out = _run(module, monkeypatch, capsys, "catalog")

    assert out["counts"]["total"] > 100
    assert out["fingerprint"]


def test_the_script_runs_an_admin_evaluation_and_passes_the_model(fake_identity, monkeypatch, capsys):
    seen: dict = {}

    async def fake_run(cases, **kwargs):
        seen.update(kwargs, cases=len(cases))
        return {"id": "eval-1", "model": kwargs.get("model"), "totals": {"cases": len(cases)},
                "by_bucket": {}, "stopped_reason": None}

    monkeypatch.setattr(runner, "run_curriculum", fake_run)
    module = _load_script(monkeypatch, "household-admin")

    out = _run(module, monkeypatch, capsys, "eval", "--limit", "2", "--model", "coder:14b")

    assert out["id"] == "eval-1"
    assert seen["model"] == "coder:14b" and seen["cases"] == 2


def test_without_an_admin_user_the_script_stops_with_a_clear_message(monkeypatch):
    module = _load_script(monkeypatch, None)
    monkeypatch.setattr(sys, "argv", ["train_jarvis.py", "--gateway", "http://testserver", "catalog"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "TRAINER_ADMIN_USER" in str(excinfo.value)


def test_the_secret_alone_is_refused_by_the_route():
    """Why the script must send X-User-Id: the gateway refuses the secret on its own."""
    client = TestClient(gateway_main.app)

    resp = client.get("/api/trainer/catalog", headers={"X-Internal-Secret": gateway_main.INTERNAL_SECRET})

    assert resp.status_code == 401
