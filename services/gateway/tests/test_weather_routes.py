"""Weather routes (docs/WEATHER.md): everyone signed in reads the forecast and
their own heads-up; the energy report and the reminder test are admins'."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import weather_routes
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}


@pytest.fixture
def calls(monkeypatch):
    class Seen(list):
        pass

    seen = Seen()

    async def authed(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "alex"
        if auth == "Bearer kid-key":
            return "jordan"
        raise HTTPException(status_code=401, detail="Authentication required")

    async def admin(request):
        user = await authed(request)
        if user != "alex":
            raise HTTPException(status_code=403, detail="Admin only")
        return user

    async def call(method, path, *, params=None, json=None):
        seen.append((method, path, params, json))
        if path == "/forecast":
            own = bool(params and params.get("user") == "jordan")
            return {"current": {"temp": 41 if own else 60}, "hourly": [], "daily": [], "personal": own}
        if path == "/heads-up":
            return {"items": [{"id": "hay", "title": "Cover the hay"}] if params["user"] == "alex" else []}
        if path == "/energy":
            return {"days": [], "model": None}
        if path == "/correlations":
            return {"metrics": [], "insights": [], "params": params}
        if path == "/integrations":
            return {"events": ["alert.started"], "services": ["geo"]}
        if path == "/metrics":
            return {"metrics": [{"name": "energy_kwh"}]}
        if path == "/reminders/test":
            return {"sent": True, "would_trigger": False, "body": "Nothing"}
        raise AssertionError(path)

    monkeypatch.setattr(gateway_main, "_require_authenticated", authed)
    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(weather_routes, "_call", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_the_forecast_needs_a_signed_in_caller_and_carries_their_own_heads_up(client, calls):
    assert client.get("/api/weather").status_code == 401
    kid = client.get("/api/weather", headers=KID).json()
    assert kid["current"]["temp"] == 41 and kid["heads_up"] == []  # jordan's own place
    assert client.get("/api/weather", headers=ADMIN).json()["heads_up"][0]["title"] == "Cover the hay"
    assert ("GET", "/heads-up", {"user": "jordan"}, None) in calls


def test_a_failed_heads_up_still_returns_the_forecast(client, calls, monkeypatch):
    real = weather_routes._call

    async def flaky(method, path, **kw):
        if path == "/heads-up":
            raise HTTPException(status_code=502, detail="down")
        return await real(method, path, **kw)

    monkeypatch.setattr(weather_routes, "_call", flaky)
    assert client.get("/api/weather", headers=KID).json()["heads_up"] == []


def test_energy_and_reminder_tests_are_admin_only(client, calls):
    assert client.get("/api/weather/energy", headers=KID).status_code == 403
    assert client.post("/api/weather/reminders/test", headers=KID, json={"rule": {"kind": "rain"}}).status_code == 403
    assert client.get("/api/weather/energy?days=9999", headers=ADMIN).status_code == 200
    assert ("GET", "/energy", {"days": 400}, None) in calls


def test_a_reminder_test_goes_to_the_admin_who_asked(client, calls):
    assert client.post("/api/weather/reminders/test", headers=ADMIN, json={}).status_code == 422
    resp = client.post("/api/weather/reminders/test", headers=ADMIN, json={"rule": {"kind": "rain", "title": "Hay"}})
    assert resp.status_code == 200
    assert calls[-1] == ("POST", "/reminders/test", None, {"rule": {"kind": "rain", "title": "Hay"}, "user": "alex"})


def test_integrations_and_correlations_are_admin_only(client, calls):
    assert client.get("/api/weather/integrations", headers=KID).status_code == 403
    assert client.get("/api/weather/correlations", headers=KID).status_code == 403
    info = client.get("/api/weather/integrations", headers=ADMIN).json()
    assert info == {"events": ["alert.started"], "services": ["geo"], "metrics": [{"name": "energy_kwh"}]}
    rep = client.get("/api/weather/correlations?days=9999&metric=eggs", headers=ADMIN).json()
    assert rep["params"] == {"days": 400, "metric": "eggs"}


def test_the_forecast_is_the_callers_own_and_household_asks_for_home(client, calls):
    body = client.get("/api/weather", headers=KID).json()
    assert body["personal"] is True and body["current"]["temp"] == 41
    assert ("GET", "/forecast", {"user": "jordan"}, None) in calls
    assert client.get("/api/weather", headers=ADMIN).json()["personal"] is False

    home = client.get("/api/weather?household=true", headers=KID).json()
    assert home["current"]["temp"] == 60 and home["personal"] is False and home["heads_up"] == []
    assert calls[-1] == ("GET", "/forecast", None, None)
