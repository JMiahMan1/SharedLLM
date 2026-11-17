"""The gateway's side of Export my data (docs/ACCOUNT_EXPORT.md).

It checks who may export or restore whom, streams one service at a time and
the location trail page by page (never all of it at once), says which parts
failed, rate limits, and writes every export and applied restore to the audit
log. The services are faked with aioresponses; nothing leaves the process.
"""
import json
import re
import sys
from unittest.mock import MagicMock

import pytest
from aioresponses import aioresponses
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.modules.setdefault("fastembed", MagicMock())

from services.gateway import account_export  # noqa: E402
from services.gateway import main as gateway  # noqa: E402

ID, HEALTH, BIBLE, GEO = "http://identity.test", "http://health.test", "http://bible.test", "http://geo.test"
MANAGERS = {"admin"}


@pytest.fixture
def env(monkeypatch):
    who = {"user": "sam", "is_admin": False}

    async def acting(request):
        return dict(who) if who.get("user") else None

    monkeypatch.setattr(gateway, "_acting_identity", acting)
    for attr, url in (("IDENTITY_SVC", ID), ("HEALTH_SVC", HEALTH), ("BIBLE_SVC", BIBLE), ("GEO_SVC", GEO)):
        monkeypatch.setattr(gateway, attr, url, raising=False)
    monkeypatch.setattr(account_export, "_last_export", {})
    monkeypatch.setattr(account_export, "_imports", {})
    app = FastAPI()
    app.include_router(account_export.router)
    with aioresponses() as mocked:
        audits: list[dict] = []

        def may_manage(url, **kwargs):
            from aioresponses import CallbackResult

            p = kwargs["params"]
            ok = p["actor"] == p["subject"] or p["actor"] in MANAGERS
            return CallbackResult(payload={"allowed": ok})

        def audit(url, **kwargs):
            from aioresponses import CallbackResult

            audits.append(kwargs["json"])
            return CallbackResult(payload={"ok": True})

        mocked.get(re.compile(rf"{ID}/api/internal/account/may-manage.*"), callback=may_manage, repeat=True)
        mocked.post(f"{ID}/api/internal/account/audit", callback=audit, repeat=True)
        yield TestClient(app), mocked, who, audits


def _serve_exports(mocked, subject="sam", geo_pages=3, fail=()):
    for base, prefix in ((ID, "/api/internal/account"), (HEALTH, "/internal/account"), (BIBLE, "/internal/account"), (GEO, "/internal/account")):
        url = f"{base}{prefix}/export/{subject}"
        if base in fail:
            mocked.get(url, status=500, repeat=True)
        else:
            mocked.get(url, payload={"collections": {"rows": [{"from": base}]}}, repeat=True)
    for page in range(geo_pages):
        nxt = (page + 1) * 2 if page + 1 < geo_pages else None
        mocked.get(
            re.compile(rf"{GEO}/internal/account/export/{subject}/location-history\?count=5000&start={page * 2}$"),
            payload={"points": [{"t": page * 2}, {"t": page * 2 + 1}], "next": nxt, "total": geo_pages * 2},
        )


def _lines(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_export_streams_manifest_sections_trail_pages_and_end(env):
    client, mocked, _, audits = env
    _serve_exports(mocked)
    resp = client.post("/api/account/export", json={})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["cache-control"] == "no-store"
    lines = _lines(resp)
    assert lines[0]["kind"] == "manifest" and lines[0]["subject"] == {"username": "sam"}
    assert any(x["what"].startswith("Passwords") for x in lines[0]["excluded"])
    services = [x["service"] for x in lines if x["kind"] == "section"]
    assert services == ["identity", "health", "bible", "geo", "geo", "geo", "geo"]
    trail = [p["t"] for x in lines if x["kind"] == "section" for p in x["collections"].get("location_history", [])]
    assert trail == [0, 1, 2, 3, 4, 5]
    assert lines[-1] == {"kind": "end", "sections": 4, "failed": []}
    assert audits == [{"actor": "sam", "target": "sam", "action": "account.export", "note": "own account"}]


def test_a_failed_service_is_named_not_hidden(env):
    client, mocked, _, _ = env
    _serve_exports(mocked, fail=(HEALTH,))
    lines = _lines(client.post("/api/account/export", json={}))
    health = next(x for x in lines if x.get("service") == "health")
    assert "error" in health and "collections" not in health
    assert lines[-1]["failed"] == ["health"]


def test_anonymous_is_refused(env):
    client, _, who, _ = env
    who["user"] = None
    assert client.post("/api/account/export", json={}).status_code == 401


def test_only_a_managing_admin_exports_someone_else(env):
    client, mocked, who, audits = env
    assert client.post("/api/account/export", json={"user": "kid"}).status_code == 403
    who.update(user="dad", is_admin=True)  # an admin identity does not know may manage kid
    assert client.post("/api/account/export", json={"user": "kid"}).status_code == 403
    who.update(user="admin", is_admin=True)
    _serve_exports(mocked, subject="kid", geo_pages=1)
    resp = client.post("/api/account/export", json={"user": "kid"})
    assert resp.status_code == 200
    assert _lines(resp)[0]["subject"] == {"username": "kid"}
    assert audits[-1] == {"actor": "admin", "target": "kid", "action": "account.export", "note": "exported by an admin for this person"}


def test_export_is_rate_limited(env, monkeypatch):
    client, mocked, _, _ = env
    _serve_exports(mocked, geo_pages=1)
    assert client.post("/api/account/export", json={}).status_code == 200
    second = client.post("/api/account/export", json={})
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) > 0


def test_import_forwards_one_service_and_audits_only_applied_restores(env):
    client, mocked, _, audits = env
    seen = []

    def take(url, **kwargs):
        from aioresponses import CallbackResult

        seen.append(kwargs["json"])
        return CallbackResult(payload={"collections": {"profile": {"add": 0, "update": 1, "conflict": 0, "unchanged": 0, "skipped": 0}}})

    mocked.post(f"{HEALTH}/internal/account/import/sam", callback=take, repeat=True)
    body = {"service": "health", "collections": {"profile": [{"user_id": "sam"}]}, "dry_run": True}
    resp = client.post("/api/account/import", json=body)
    assert resp.status_code == 200
    assert resp.json()["collections"]["profile"]["update"] == 1
    assert seen[0]["dry_run"] is True and seen[0]["replace_conflicts"] is False
    assert audits == []
    client.post("/api/account/import", json={**body, "dry_run": False, "replace_conflicts": True})
    assert seen[1]["replace_conflicts"] is True
    assert audits == [{"actor": "sam", "target": "sam", "action": "account.import", "note": "health: own account"}]


def test_import_refuses_unknown_service_and_someone_elses_account(env):
    client, _, _, _ = env
    assert client.post("/api/account/import", json={"service": "telemetry", "collections": {}}).status_code == 422
    assert client.post("/api/account/import", json={"service": "health", "collections": {}, "user": "kid"}).status_code == 403


def test_import_is_rate_limited(env, monkeypatch):
    client, mocked, _, _ = env
    monkeypatch.setattr(account_export, "IMPORT_MAX_PER_WINDOW", 2)
    mocked.post(f"{BIBLE}/internal/account/import/sam", payload={"collections": {}}, repeat=True)
    body = {"service": "bible", "collections": {}, "dry_run": True}
    assert client.post("/api/account/import", json=body).status_code == 200
    assert client.post("/api/account/import", json=body).status_code == 200
    assert client.post("/api/account/import", json=body).status_code == 429


@pytest.mark.asyncio
async def test_the_stream_holds_one_page_at_a_time(monkeypatch):
    """Each page is handed on before the next is asked for, so memory stays one page deep."""
    events: list[str] = []

    async def fake_get_json(session, url, params=None):
        events.append(f"get {url.rsplit('/', 1)[-1]} {params and params.get('start')}")
        if url.endswith("location-history"):
            start = params["start"]
            return {"points": [{"t": start}], "next": start + 1 if start < 2 else None}
        return {"collections": {}}

    monkeypatch.setattr(account_export, "_get_json", fake_get_json)
    for attr in ("IDENTITY_SVC", "HEALTH_SVC", "BIBLE_SVC", "GEO_SVC"):
        monkeypatch.setattr(gateway, attr, "http://svc.test", raising=False)
    async for line in account_export.export_stream("sam", "sam"):
        record = json.loads(line)
        if record.get("collections", {}).get("location_history"):
            events.append(f"yield {record['collections']['location_history'][0]['t']}")
    trail = [e for e in events if "location-history" in e or e.startswith("yield")]
    assert trail == [
        "get location-history 0", "yield 0",
        "get location-history 1", "yield 1",
        "get location-history 2", "yield 2",
    ]
