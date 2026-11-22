"""A child's photo check while the household's GPU box is off: kept, not skipped, and quiet.

The check must stay on the local vision model. While it is unreachable the
phone is told to keep the picture and try later (503 with Retry-After), at
once, and nobody is paged about it. Host names are fake.
"""
import errno
import time

import aiohttp
import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import guardian, llm
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import MEDIA_SCAN_RETRY_OFFLINE_S, app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
KIDS = {"kid": {"username": "kid", "display_name": "Emma", "parents": ["dad", "mom"], "monitoring": "severe",
                "device_kinds": ["phone"]}}
PHOTO = {"user": "kid", "file_name": "p.jpg", "media_type": "image", "image_b64": "ZXNwaG9tZQ=="}


def _unreachable() -> aiohttp.ClientConnectorError:
    key = aiohttp.client_reqrep.ConnectionKey("gpu-box.test", 11434, False, True, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(errno.EHOSTUNREACH, "No route to host"))


@pytest.fixture
def offline(monkeypatch):
    sent: list[dict] = []
    calls = {"n": 0}

    async def _children(_session):
        return KIDS

    async def _deliver(body):
        sent.append(body)
        return {"status": "SUCCESS"}

    async def _settings(_session):
        return {"vision_ocr_proxy_url": "http://gpu-box.test:11434", "vision_ocr_model": "vl",
                "llm_local_url": "http://gpu-box.test:11434", "guardian_monitor_model": "m"}

    async def _box_off(_session, url, payload, timeout, **_kw):
        calls["n"] += 1
        raise _unreachable()

    monkeypatch.setattr("services.telemetry.main._redis", lambda: FakeRedis())
    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    monkeypatch.setattr(llm, "chat_content", _box_off)
    with TestClient(app) as client:
        yield client, sent, calls


def test_a_photo_is_sent_back_with_retry_after_and_nobody_is_paged(offline):
    client, sent, calls = offline
    resp = client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO)
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == str(MEDIA_SCAN_RETRY_OFFLINE_S)
    body = resp.json()
    assert body["retry_after_s"] == MEDIA_SCAN_RETRY_OFFLINE_S and body["scanned"] is False
    assert "gpu-box" not in resp.text and "No route" not in resp.text
    assert sent == []
    assert client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"] == []
    assert llm.LOCAL_MODEL.is_down()
    assert calls["n"] == 1


def test_later_photos_fail_fast_without_calling_the_box(offline):
    client, sent, calls = offline
    client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO)
    started = time.monotonic()
    for _ in range(5):
        assert client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO).status_code == 503
    assert time.monotonic() - started < 2.0
    assert calls["n"] == 1  # the breaker answered; no connect attempts, no warning per photo
    assert sent == []


def test_it_never_goes_anywhere_but_the_local_model(offline, monkeypatch):
    """Not even with the breaker open: the photo check has no other model to try."""
    client, _sent, _calls = offline
    seen_urls: list[str] = []

    async def _record(_session, url, payload, timeout, **_kw):
        seen_urls.append(url)
        raise _unreachable()

    monkeypatch.setattr(llm, "chat_content", _record)
    client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO)
    client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO)
    assert seen_urls and all(u.startswith("http://gpu-box.test:11434/") for u in seen_urls)


def test_content_monitoring_falls_back_to_the_phrase_list_quietly(offline, caplog):
    client, sent, _calls = offline
    llm.LOCAL_MODEL.mark_down("refused")
    caplog.set_level("WARNING")
    caplog.clear()
    client.post("/api/telemetry/activity", headers=HEADERS, json={
        "user": "kid", "feature": "sms", "item": "Unknown", "action": "incoming", "at": "2026-10-21T10:00:00Z",
        "detail": {"body": "i want to die"}})
    # The phrase list still pages the parents: safety does not wait for the model.
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert not [r for r in caplog.records if "content monitoring" in r.getMessage()]


def test_guardian_never_reaches_the_online_fallback_even_wide_open(offline, monkeypatch):
    """Photo checks and content monitoring with the breaker open and the online fallback
    configured as wide as it goes (every feature, a provider): nothing is sent online."""
    from services.gateway import cloud_fallback as cf

    client, _sent, _calls = offline
    online: list[object] = []

    async def record(provider, messages):
        online.append(messages)
        return "should never happen"

    provider = cf.CloudProvider(url="https://cloud.test/v1/chat/completions", api_key="k", model="m:free", name="custom")
    monkeypatch.setattr(cf, "CLIENT", cf.CloudFallbackClient(frozenset(cf.FEATURES), provider, lambda: True))
    monkeypatch.setattr(cf, "_send", record)
    llm.LOCAL_MODEL.mark_down("refused")
    assert client.post("/api/telemetry/media/scan", headers=HEADERS, json=PHOTO).status_code == 503
    client.post("/api/telemetry/activity", headers=HEADERS, json={
        "user": "kid", "feature": "sms", "item": "Unknown", "action": "incoming", "at": "2026-10-21T10:00:00Z",
        "detail": {"body": "meet me after school"}})
    assert online == []
