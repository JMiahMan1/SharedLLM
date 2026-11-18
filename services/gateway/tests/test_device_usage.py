"""Every command a home hub runs counts as use of its devices in the RAG device index."""
import asyncio
import json

import pytest
import websockets

from services.gateway import device_usage


def test_the_entity_ids_of_a_command():
    assert device_usage.entity_ids_of({"entity_ids": ["light.a", "light.b"], "service_data": {}}) == ["light.a", "light.b"]
    # Home Assistant's event has only what the caller named.
    assert device_usage.entity_ids_of({"service_data": {"entity_id": "light.a"}}) == ["light.a"]
    assert device_usage.entity_ids_of({"service_data": {"entity_id": ["light.a", "all"]}}) == ["light.a"]
    assert device_usage.entity_ids_of({"service_data": {"target": {"entity_id": "switch.fan"}}}) == ["switch.fan"]
    assert device_usage.entity_ids_of({"service_data": {"area_id": "kitchen"}}) == []


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        return _Resp(200)


@pytest.mark.asyncio
async def test_collected_ids_are_reported_once_per_hub_and_then_forgotten():
    usage = device_usage.DeviceUsage("http://rag.example.test:8004/", "s3cret")
    usage.note("homehub", {"entity_ids": ["light.a"]})
    usage.note("homehub", {"entity_ids": ["light.a", "light.b"]})
    usage.note("home_assistant", {"service_data": {"entity_id": "switch.c"}})
    session = _Session()
    assert await usage.flush(session) == 3
    sent = {body["source"]: body["entity_ids"] for _, body, _ in session.posts}
    assert sent == {"homehub": ["light.a", "light.b"], "home_assistant": ["switch.c"]}
    assert all(url == "http://rag.example.test:8004/rag/devices/used" for url, _, _ in session.posts)
    assert all(h == {"X-Internal-Secret": "s3cret"} for _, _, h in session.posts)
    assert await usage.flush(session) == 0


@pytest.mark.asyncio
async def test_the_listener_logs_in_subscribes_and_collects_commands(monkeypatch):
    got_subscribe = asyncio.Event()

    async def hub(ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        assert auth == {"type": "auth", "access_token": "tok"}
        await ws.send(json.dumps({"type": "auth_ok"}))
        sub = json.loads(await ws.recv())
        assert sub["type"] == "subscribe_events" and sub["event_type"] == "call_service"
        got_subscribe.set()
        await ws.send(json.dumps({"id": 1, "type": "event", "event": {
            "event_type": "call_service", "data": {"domain": "light", "service": "turn_off",
                                                   "entity_ids": ["light.kitchen_1"]}}}))
        await asyncio.sleep(5)

    async with websockets.serve(hub, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        usage = device_usage.DeviceUsage("http://rag.example.test", "s")
        task = asyncio.create_task(usage.listen("homehub", f"http://127.0.0.1:{port}", "tok"))
        await asyncio.wait_for(got_subscribe.wait(), 5)
        for _ in range(50):
            if usage.pending:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert usage.pending == {"homehub": {"light.kitchen_1"}}


def test_only_hubs_that_are_on_are_followed(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr(config, "HA_URL", "")
    assert [h[0] for h in device_usage.hubs_to_follow()] == ["homehub"]
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    assert device_usage.hubs_to_follow() == []
