"""Health hub proxies added for the later phases: each acts as the caller only."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    recorded: dict = {"health": []}

    async def _ident(_request):
        return {"user": "dad", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)

    class _Health:
        async def request(self, method, url, **kw):
            recorded["health"].append((method, url, kw))
            return _Resp({"ok": True})

    @asynccontextmanager
    async def fake_health():
        yield _Health()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_health)
    return recorded


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.mark.parametrize(
    ("method", "path", "body", "upstream"),
    [
        ("GET", "/api/health/streaks", None, "/streaks"),
        ("GET", "/api/health/goals", None, "/goals"),
        ("POST", "/api/health/goals", {"kind": "water_days", "day": "2026-10-21", "target_days": 3}, "/goals"),
        ("DELETE", "/api/health/goals/2", None, "/goals/2"),
        ("GET", "/api/health/fasting", None, "/fasting"),
        ("POST", "/api/health/fasting/start", {"target_hours": 14}, "/fasting/start"),
        ("POST", "/api/health/fasting/stop", {}, "/fasting/stop"),
        ("DELETE", "/api/health/fasting/3", None, "/fasting/3"),
        ("POST", "/api/health/food/estimate", {"ingredients": ["2 cups broccoli"]}, "/food/estimate"),
    ],
)
def test_routes_forward_as_the_caller(client, calls, method, path, body, upstream):
    response = client.request(method, path, json=body)
    assert response.json() == {"ok": True}
    sent_method, url, kw = calls["health"][0]
    assert sent_method == method and url.endswith(upstream)
    assert kw["headers"]["X-User-Id"] == "dad"
    if body is not None:
        assert kw["json"] == body


def test_a_new_cheer_sends_one_health_notice(client, monkeypatch):
    async def _ident(_request):
        return {"user": "mom", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)
    answers = [
        {"id": 5, "from_user": "mom", "to_user": "sam", "emoji": "👏", "label": "Well done", "duplicate": False},
        {"id": 5, "from_user": "mom", "to_user": "sam", "emoji": "👏", "label": "Well done", "duplicate": True},
    ]

    class _Health:
        async def request(self, method, url, **kw):
            return _Resp(answers.pop(0))

    @asynccontextmanager
    async def fake():
        yield _Health()

    notices: list[dict] = []

    async def _send(payload, **_kw):
        notices.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", _send)
    body = {"to_user": "sam", "kind": "well_done", "day": "2026-10-22"}
    client.post("/api/health/cheers", json=body)
    client.post("/api/health/cheers", json=body)
    assert len(notices) == 1
    assert notices[0]["user"] == "sam" and notices[0]["category"] == "health"
    assert notices[0]["title"] == "Mom cheered your day"


def _ocr_fake(monkeypatch, answer, status=200):
    sent: list[dict] = []

    async def _creds(_request, _as_user=None):
        return {"user": "dad", "is_admin": False}

    class _Exec:
        async def post(self, url, json=None, **kw):
            sent.append({"url": url, "json": json})
            return _Resp(answer, status=status)

    @asynccontextmanager
    async def fake():
        yield _Exec()

    monkeypatch.setattr(gateway_main, "_resolve_acting_identity", _creds)
    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return sent


def test_a_label_photo_becomes_a_draft_and_nothing_is_saved(client, monkeypatch):
    sent = _ocr_fake(monkeypatch, {"status": "SUCCESS", "detail": {"full_text": "Calories 120\nTotal Fat 2g\nTotal Carbohydrate 20g\nProtein 5g"}})
    body = client.post("/api/health/food/photo", files={"image": ("label.jpg", b"jpeg-bytes", "image/jpeg")}).json()
    assert body["draft"]["calories"] == 120 and body["missing"] == []
    assert body["text"].startswith("Calories 120")
    assert sent[0]["url"].endswith("/execute/ocr") and sent[0]["json"]["task"] == "document"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_an_unreadable_photo_says_so(client, monkeypatch):
    _ocr_fake(monkeypatch, {"status": "FAILURE", "message": "OCR failed: vision_ocr_model not configured"})
    response = client.post("/api/health/food/photo", files={"image": ("label.jpg", b"jpeg-bytes", "image/jpeg")})
    assert response.status_code == 502
    assert "vision_ocr_model" in response.json()["detail"]
    assert client.post("/api/health/food/photo", files={"other": ("x.jpg", b"x", "image/jpeg")}).status_code == 422


def _coach_store(monkeypatch, summary):
    seen: list[tuple[str, dict]] = []

    class _Store:
        async def request(self, method, url, **kw):
            seen.append((url, kw))
            if url.endswith("/summary"):
                return _Resp(summary)
            return _Resp({"averages": {"sleep_minutes": 420}, "goals": []})

    @asynccontextmanager
    async def fake():
        yield _Store()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return seen


def _summary(metrics_on=True):
    on = {"coach": metrics_on, "sleep": True}
    return {
        "profile": {"kid_mode": False, "metrics": on},
        "sleep": {"last_night": {"duration_minutes": 450}, "score": None, "need_minutes": 480},
    }


def test_the_coach_answers_from_the_callers_own_store(monkeypatch):
    import asyncio

    seen = _coach_store(monkeypatch, _summary())
    text = asyncio.run(gateway_main._health_coach_answer("How did I sleep?", "dad"))
    assert text.startswith("You slept 7 h 30 min last night.")
    assert all(kw["headers"]["X-User-Id"] == "dad" for _url, kw in seen)


def test_the_coach_stays_quiet_when_switched_off_or_off_topic(monkeypatch):
    import asyncio

    seen = _coach_store(monkeypatch, _summary(metrics_on=False))
    assert asyncio.run(gateway_main._health_coach_answer("How did I sleep?", "dad")) is None
    seen.clear()
    assert asyncio.run(gateway_main._health_coach_answer("play some music", "dad")) is None
    assert seen == []


def test_active_minutes_composes_geo_and_the_health_store(client, monkeypatch):
    async def _ident(_request):
        return {"user": "dad", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)
    monkeypatch.setenv("TIMEZONE", "UTC")
    seen: list[tuple[str, dict]] = []

    class _Routed:
        async def request(self, method, url, **kw):
            seen.append((url, kw))
            if url.endswith("/steps/sources"):
                return _Resp({"hourly": {"phone": {"2026-10-20:07": 4000}}})
            if url.endswith("/workouts"):
                return _Resp({"workouts": []})
            if url.endswith("/profile"):
                return _Resp({"birth_year": 1986, "weekly_active_minutes": 150})
            return _Resp({"samples": []})

    @asynccontextmanager
    async def fake():
        yield _Routed()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    body = client.get("/api/health/active-minutes", params={"day": "2026-10-21"}).json()
    assert body["total_minutes"] == 30
    assert body["target_minutes"] == 150 and body["percent"] == 20
    assert body["unavailable"] == []
    steps_call = next(kw for url, kw in seen if url.endswith("/steps/sources"))
    assert steps_call["params"]["user_id"] == "dad" and steps_call["params"]["days"] == 3
    assert client.get("/api/health/active-minutes", params={"day": "21-10-2026"}).status_code == 422


# --- the digital family scope (an adult opens their own phone use) ----------


def _family_client(monkeypatch, days):
    """Patch the caller, the health answer and the telemetry read for /family."""
    async def _ident(_request):
        return {"user": "michele", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)
    family = {
        "viewer": "michele",
        "day": "2026-10-21",
        "people": [{"user_id": "jeremiah", "scopes": ["digital"], "digital": {"shared": True}}],
    }

    class _Health:
        async def request(self, method, url, **kw):
            return _Resp(family)

    @asynccontextmanager
    async def fake_health():
        yield _Health()

    seen: list[tuple[str, dict]] = []

    class _Telemetry:
        async def get(self, url, **kw):
            seen.append((url, kw))
            if days is None:
                raise RuntimeError("telemetry down")
            return _Resp({"status": "SUCCESS", "days": days})

    @asynccontextmanager
    async def fake_telemetry():
        yield _Telemetry()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_health)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake_telemetry)
    return seen


def test_the_digital_scope_is_filled_from_telemetry(client, monkeypatch):
    seen = _family_client(monkeypatch, [{"day": "2026-10-21", "millis": 3_600_000}])
    body = client.get("/api/health/family", params={"day": "2026-10-21"}).json()
    assert body["people"][0]["digital"] == {
        "shared": True, "available": True, "screen_time_min": 60, "day": "2026-10-21"}
    assert any(url.endswith("/api/telemetry/usage/jeremiah") for url, _kw in seen)


def test_the_digital_scope_says_when_no_day_was_reported(client, monkeypatch):
    _family_client(monkeypatch, [])
    digital = client.get("/api/health/family", params={"day": "2026-10-21"}).json()["people"][0]["digital"]
    assert digital["available"] is True and digital["screen_time_min"] is None


def test_the_digital_scope_is_unavailable_when_telemetry_is_down(client, monkeypatch):
    _family_client(monkeypatch, None)
    digital = client.get("/api/health/family", params={"day": "2026-10-21"}).json()["people"][0]["digital"]
    assert digital["available"] is False and digital["screen_time_min"] is None
