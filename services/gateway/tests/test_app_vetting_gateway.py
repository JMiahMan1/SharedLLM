"""App vetting through the gateway: minors' devices report, parents read."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import app_vetting_routes as routes
from services.gateway import main as gateway_main
from services.gateway.main import app

DEVICE = "a1b2c3d4e5f6a7b8c9d0"
ROLES = {"kid": "child", "teen": "teen", "dad": "father", "grown": "adult_child"}
POLICY = {"username": "kid", "display_name": "Sam", "birth_date": "2016-01-01", "age": 10,
          "default_max_rating": "Everyone 10+", "max_rating": None, "effective_max_rating": "Everyone 10+",
          "apps": {}, "unused_days": 30, "dismissed": {}, "kept": {}, "notify_level": "high",
          "blocked_apps": [], "uninstall_apps": [], "can_uninstall": False}
DAY_MS = 86_400_000


@pytest.fixture
def wired(monkeypatch):
    who = {"user": "kid"}
    identity: list[tuple] = []
    telemetry: list[tuple] = []
    answers: dict = {}

    async def _auth(request):
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return who["user"]

    async def _acting(request):
        return {"user": who["user"]} if who["user"] else None

    async def _identity(method, path, request=None, **kw):
        identity.append((method, path, request is not None, kw.get("json")))
        if path.startswith("/api/internal/family-role/"):
            user = path.rsplit("/", 1)[1]
            return 200, {"username": user, "family_role": ROLES.get(user, "other")}
        return answers.get(("identity", method, path), (200, {}))

    async def _telemetry(method, path, **kw):
        telemetry.append((method, path, kw.get("json")))
        return answers.get(("telemetry", method, path), (200, {}))

    monkeypatch.setattr(gateway_main, "_require_authenticated", _auth)
    monkeypatch.setattr(gateway_main, "_acting_identity", _acting)
    monkeypatch.setattr(routes, "identity_call", _identity)
    monkeypatch.setattr(routes, "telemetry_call", _telemetry)
    return {"client": TestClient(app, headers={"Authorization": "Bearer key"}), "who": who,
            "identity": identity, "telemetry": telemetry, "answers": answers}


def _apps(n=1):
    return [{"package": "com.roblox.client", "label": "Roblox", "installer": "com.android.vending"}] * n


def test_a_childs_device_reports_its_apps_as_the_child(wired):
    wired["answers"][("telemetry", "POST", "/api/telemetry/apps/inventory")] = (200, {"apps": 1})
    r = wired["client"].post("/api/apps/inventory", json={"device_key": DEVICE, "apps": _apps(), "usage_access": True,
                                                          "user": "someone-else"})
    assert r.status_code == 200, r.text
    method, path, sent = wired["telemetry"][0]
    assert (method, path) == ("POST", "/api/telemetry/apps/inventory")
    assert sent["user"] == "kid" and sent["device"] == DEVICE and sent["usage_access"] is True


@pytest.mark.parametrize("user", ["dad", "grown"])
def test_a_grown_members_device_is_refused(wired, user):
    wired["who"]["user"] = user
    r = wired["client"].post("/api/apps/inventory", json={"device_key": DEVICE, "apps": _apps()})
    assert r.status_code == 403
    assert wired["telemetry"] == []


def test_a_teen_reports_too(wired):
    wired["who"]["user"] = "teen"
    assert wired["client"].post("/api/apps/inventory", json={"device_key": DEVICE, "apps": []}).status_code == 200


def test_reporting_needs_a_signed_in_device(wired):
    wired["who"]["user"] = None
    assert wired["client"].post("/api/apps/inventory", json={"device_key": DEVICE, "apps": []}).status_code == 401


@pytest.mark.parametrize("body", [{"device_key": "short", "apps": []}, {"device_key": DEVICE, "apps": "all"},
                                  {"device_key": DEVICE, "apps": _apps(501)}])
def test_a_bad_report_is_refused(wired, body):
    assert wired["client"].post("/api/apps/inventory", json=body).status_code == 422
    assert wired["telemetry"] == []


@pytest.mark.parametrize("status", [403, 404])
def test_the_card_follows_identitys_answer(wired, status):
    wired["who"]["user"] = "sib"
    wired["answers"][("identity", "GET", "/api/apps/kid/policy")] = (status, {"detail": "no"})
    assert wired["client"].get("/api/guardian/apps/kid").status_code == status
    assert wired["telemetry"] == []  # nothing about the apps is read for someone refused


def test_the_card_asks_identity_as_the_caller(wired):
    wired["who"]["user"] = "dad"
    wired["answers"][("identity", "GET", "/api/apps/kid/policy")] = (200, POLICY)
    wired["answers"][("telemetry", "GET", "/api/telemetry/apps/kid")] = (200, {"apps": [], "devices": []})
    assert wired["client"].get("/api/guardian/apps/kid").status_code == 200
    assert wired["identity"][0][:3] == ("GET", "/api/apps/kid/policy", True)


def test_the_card_puts_questionable_apps_first_and_lists_unused_ones():
    now = 1_800_000_000_000
    data = {
        "devices": [{"device": DEVICE}],
        "apps": [
            {"package": "com.a.calm", "label": "Calm", "installer": "com.android.vending", "launchable": True,
             "last_used": now - DAY_MS, "devices": [DEVICE], "permissions": []},
            {"package": "com.roblox.client", "label": "Roblox", "installer": "com.android.vending", "launchable": True,
             "last_used": now - 60 * DAY_MS, "devices": [DEVICE], "permissions": []},
        ],
        "catalog": {
            "com.a.calm": {"listing": {"status": "found", "hash": "h", "listing": {"content_rating": "Everyone"}},
                           "review": {"status": "ready", "listing_hash": "h", "summary": "Fine.", "concerns": []}},
            "com.roblox.client": {"listing": {"status": "found", "hash": "r", "listing": {"content_rating": "Teen"}},
                                  "review": {"status": "ready", "listing_hash": "r", "summary": "Chat.", "concerns": [
                                      {"category": "contact_strangers", "severity": "high", "why": "Chat", "evidence": []}]}},
        },
    }
    card = routes.build_card(POLICY, data, now)
    assert [a["package"] for a in card["apps"]] == ["com.roblox.client", "com.a.calm"]
    assert card["attention"] == 1
    assert {f["reason"] for f in card["apps"][0]["attention"]} == {"over_age", "concern"}
    assert [u["package"] for u in card["unused"]] == ["com.roblox.client"]


def test_a_parent_sees_verdicts_on_a_childs_app_requests_and_the_child_does_not(wired, monkeypatch):
    rows = [
        {"id": 1, "username": "kid", "kind": "app", "target": "com.roblox.client", "label": "Roblox", "status": "pending"},
        {"id": 2, "username": "kid", "kind": "site", "target": "example.com", "label": "", "status": "pending"},
    ]

    async def _as_caller(request, method, path, **kw):
        return 200, {"requests": [dict(r) for r in rows]}

    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    wired["answers"][("identity", "GET", "/api/internal/guardian/children")] = (200, {"children": [
        {"username": "kid", "birth_date": "2016-01-01", "app_policy": {}}]})
    wired["answers"][("telemetry", "POST", "/api/telemetry/apps/catalog")] = (200, {"catalog": {
        "com.roblox.client": {"listing": {"status": "found", "hash": "r", "listing": {"content_rating": "Teen"}},
                              "review": {"status": "ready", "listing_hash": "r", "summary": "Chat.", "concerns": []}}}})
    wired["who"]["user"] = "dad"
    got = wired["client"].get("/api/guard/requests").json()["requests"]
    vetting = got[0]["vetting"]
    assert vetting["over_age"] is True and vetting["rating"] == "Teen" and vetting["limit"] == "Everyone 10+"
    assert "Rated Teen, above the Everyone 10+ limit" in vetting["line"]
    assert "vetting" not in got[1]
    wired["who"]["user"] = "kid"
    mine = wired["client"].get("/api/guard/requests").json()["requests"]
    assert all("vetting" not in r for r in mine)


async def test_verdicts_that_cannot_be_read_say_so(wired):
    wired["answers"][("identity", "GET", "/api/internal/guardian/children")] = (200, {"children": []})
    wired["answers"][("telemetry", "POST", "/api/telemetry/apps/catalog")] = (503, {})
    rows = await routes.vet_requests("dad", [{"username": "kid", "kind": "app", "target": "com.x.y"}])
    assert rows[0]["vetting"]["lookup"] == "unavailable"


def test_an_app_request_notice_carries_the_verdict(wired, monkeypatch):
    sent = []

    async def _as_caller(request, method, path, **kw):
        return 200, {"request": {"id": 9, "username": "kid", "kind": "app", "target": "com.roblox.client",
                                 "label": "Roblox"}, "notify": ["dad", "mom"]}

    async def _send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    monkeypatch.setattr(gateway_main._notify, "send", _send)
    wired["answers"][("identity", "GET", "/api/internal/guardian/children")] = (200, {"children": [
        {"username": "kid", "birth_date": "2016-01-01", "app_policy": {}}]})
    wired["answers"][("telemetry", "POST", "/api/telemetry/apps/catalog")] = (200, {"catalog": {
        "com.roblox.client": {"listing": {"status": "pending"}, "review": None}}})
    r = wired["client"].post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client"})
    assert r.status_code == 200
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "checking its Play listing" in sent[0]["body"]
    assert sent[0]["tag"] == "guard-app:kid:com.roblox.client" and sent[0]["replace"] is True
    waiting = next(j for m, p, j in wired["telemetry"] if p == "/api/telemetry/apps/catalog")
    assert waiting["waiting"] == {"child": "kid", "request_id": 9, "label": "Roblox"}
