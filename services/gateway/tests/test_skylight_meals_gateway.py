"""Gateway contract for the Skylight Meals proxy.

The recipes/plan/grocery routes are thin: authenticate, honour the per-user
Skylight switch, forward the caller's identity and the body to execution, and
pass the answer back. These pin that contract and that a disabled account never
reaches the execution service.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_execution(monkeypatch, payload=None):
    captured: dict = {"calls": []}
    execution_svc = str(gateway_main.EXECUTION_SVC)

    class _Resp:
        status = 200

        def __init__(self):
            self._payload = payload if payload is not None else {"status": "SUCCESS"}
            self.text = json.dumps(self._payload)

        async def json(self):
            return self._payload

        async def text(self):
            return self.text

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(execution_svc):
                captured["calls"].append(
                    {"verb": verb, "url": url, "params": kwargs.get("params"), "json": kwargs.get("json")}
                )
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def resolver(monkeypatch):
    def _install(user="jeremiah", is_admin=True, skylight_enabled=True, key="test-token"):
        async def _resolve(request, body=None):
            if request.headers.get("Authorization", "") != f"Bearer {key}":
                raise HTTPException(status_code=401, detail="Authentication required")
            return {"user": user, "is_admin": is_admin, "skylight_enabled": skylight_enabled}

        monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
        return key

    return _install


@pytest.fixture
def meals_client(resolver):
    def _make(user="jeremiah", is_admin=True, skylight_enabled=True, key="test-token"):
        resolver(user=user, is_admin=is_admin, skylight_enabled=skylight_enabled, key=key)
        return TestClient(app, headers={"Authorization": f"Bearer {key}"}, raise_server_exceptions=False)

    return _make


def test_status_is_forwarded_with_the_caller_identity(meals_client, monkeypatch):
    captured = _patch_execution(monkeypatch, {"status": "SUCCESS", "configured": True, "categories": []})
    resp = meals_client(user="mom").get("/api/integrations/skylight/meals")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert captured["calls"][0]["url"].endswith("/api/integrations/skylight/meals")
    assert captured["calls"][0]["params"]["user"] == "mom"


def test_week_forwards_the_date_window(meals_client, monkeypatch):
    captured = _patch_execution(monkeypatch, {"status": "SUCCESS", "sittings": []})
    resp = meals_client().get(
        "/api/integrations/skylight/meals/week",
        params={"date_min": "2026-10-26", "date_max": "2026-11-01"},
    )
    assert resp.status_code == 200
    params = captured["calls"][0]["params"]
    assert params["date_min"] == "2026-10-26"
    assert params["date_max"] == "2026-11-01"


def test_sending_a_recipe_forwards_the_body_and_identity(meals_client, monkeypatch):
    captured = _patch_execution(monkeypatch, {"status": "SUCCESS", "recipe_id": "5"})
    resp = meals_client(user="mom").post(
        "/api/integrations/skylight/meals/recipe",
        json={"title": "Pancakes", "body": "## Ingredients\n- [ ] flour"},
    )
    assert resp.status_code == 200
    call = captured["calls"][0]
    assert call["url"].endswith("/api/integrations/skylight/meals/recipe")
    assert call["params"]["user"] == "mom"
    assert call["json"]["title"] == "Pancakes"


def test_pushing_the_week_and_the_grocery_list_forward(meals_client, monkeypatch):
    captured = _patch_execution(monkeypatch, {"status": "SUCCESS"})
    meals_client().post(
        "/api/integrations/skylight/meals/plan",
        json={"days": [{"date": "2026-10-31", "title": "Chili"}]},
    )
    meals_client().post(
        "/api/integrations/skylight/meals/grocery",
        json={"items": ["2 lb chicken"]},
    )
    urls = [c["url"] for c in captured["calls"]]
    assert any(url.endswith("/meals/plan") for url in urls)
    assert any(url.endswith("/meals/grocery") for url in urls)


def test_a_disabled_account_is_told_so_and_forwards_nothing(meals_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = meals_client(user="michele", is_admin=False, skylight_enabled=False).get(
        "/api/integrations/skylight/meals"
    )
    assert resp.status_code == 400
    assert "disabled" in resp.json()["message"].lower()
    assert captured["calls"] == []


def test_anonymous_callers_are_rejected(resolver, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resolver()
    anon = TestClient(app, raise_server_exceptions=False)
    assert anon.get("/api/integrations/skylight/meals").status_code == 401
    assert captured["calls"] == []
