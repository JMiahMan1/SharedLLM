import json
import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3ZlcyE=")

import pytest

from services.telemetry import push

from fakes import FakeRedis


@pytest.fixture(autouse=True)
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(push, "_redis", lambda: fake)
    return fake


def sub(endpoint: str) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}


async def test_subscription_round_trip(rc):
    await push.save_subscription("jeremiah", sub("https://push.example/1"))
    subs = await push.list_subscriptions("jeremiah")
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example/1"

    assert await push.remove_subscription("jeremiah", "https://push.example/1") is True
    assert await push.list_subscriptions("jeremiah") == []


async def test_duplicate_subscription_is_stored_once(rc):
    await push.save_subscription("jeremiah", sub("https://push.example/1"))
    await push.save_subscription("jeremiah", sub("https://push.example/1"))
    assert len(await push.list_subscriptions("jeremiah")) == 1


async def test_vapid_keys_are_generated_once_and_reused(rc):
    first = await push.ensure_vapid_keys()
    assert first and first["public_key"]
    second = await push.ensure_vapid_keys()
    assert second["public_key"] == first["public_key"]
    assert second["private_key"] == first["private_key"]


async def test_vapid_public_key_is_exposed(rc):
    key = await push.vapid_public_key()
    assert isinstance(key, str) and key


async def test_send_without_subscriptions_is_a_noop(rc, monkeypatch):
    async def fail(*_args, **_kwargs):
        raise AssertionError("should not attempt delivery")

    monkeypatch.setattr(push, "_send_webpush", fail)
    result = await push.send_to_user("jeremiah", "Title", "Body")
    assert result == {"webpush": 0, "fcm": False, "pruned": 0}


async def test_successful_webpush_is_counted(rc, monkeypatch):
    await push.save_subscription("jeremiah", sub("https://push.example/1"))

    async def ok(_subscription, _payload, _keys):
        return True

    monkeypatch.setattr(push, "_send_webpush", ok)
    result = await push.send_to_user("jeremiah", "Report ready", "You hit your goal")
    assert result["webpush"] == 1
    assert result["pruned"] == 0
    assert len(await push.list_subscriptions("jeremiah")) == 1


async def test_dead_subscription_is_pruned_not_retried_forever(rc, monkeypatch):
    await push.save_subscription("jeremiah", sub("https://push.example/gone"))

    async def gone(_subscription, _payload, _keys):
        return False  # 404/410 from the push service

    monkeypatch.setattr(push, "_send_webpush", gone)
    result = await push.send_to_user("jeremiah", "Report ready", "body")
    assert result["pruned"] == 1
    assert await push.list_subscriptions("jeremiah") == []


async def test_fcm_is_inert_without_credentials(rc, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("TELEMETRY_FCM_CREDENTIALS", raising=False)
    result = await push.send_to_user("jeremiah", "Title", "Body")
    assert result["fcm"] is False


async def test_fcm_uses_registered_token_when_available(rc, monkeypatch):
    monkeypatch.setenv("TELEMETRY_FCM_CREDENTIALS", "/tmp/fake.json")
    rc.strings["tel:push:fcm_token:jeremiah"] = "device-token"
    # firebase-admin is not installed in tests, so this must degrade quietly
    result = await push.send_to_user("jeremiah", "Title", "Body")
    assert result["fcm"] is False


async def test_send_never_raises_when_transport_explodes(rc, monkeypatch):
    await push.save_subscription("jeremiah", sub("https://push.example/1"))

    async def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(push, "_send_webpush", boom)
    result = await push.send_to_user("jeremiah", "Title", "Body")
    assert result["webpush"] == 0
