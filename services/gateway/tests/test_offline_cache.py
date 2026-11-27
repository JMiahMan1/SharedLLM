"""The offline snapshot: built as each member (their sharing decides), sealed for them, versioned by content;
and offline shopping edits applied once by the list's own rules."""
import base64
import gzip
import json
import os
import time

import pytest

from services.gateway import offline_cache, offline_relay
from services.gateway.tests.test_offline_relay import KEYS, FakeRedis


def open_item(item, member):
    _, seal_key = offline_relay.derive_keys(KEYS[member])
    inner = offline_relay.open_sealed(seal_key, item["env"])
    return inner, json.loads(gzip.decompress(base64.b64decode(inner["data"])))


@pytest.fixture
def routes(monkeypatch):
    """Each member sees only what the server's routes give them (here: their own name in the data)."""
    calls: list[tuple] = []

    async def _call(method, path, api_key, body):
        member = next(m for m, k in KEYS.items() if k == api_key)
        calls.append((member, path))
        if "location/all" in path:
            return 200, {"locations": [{"user": member, "lat": 1.0}]}
        if "notes/read" in path:
            return 200, {"status": "SUCCESS", "message": "- [ ] milk"}
        if "bible" in path:
            return 500, None
        return 200, {"for": member}

    monkeypatch.setattr(offline_relay, "_call_self", _call)
    return calls


@pytest.mark.asyncio
async def test_items_are_made_as_each_member_and_only_they_can_open_theirs(routes):
    conn = FakeRedis()
    items = await offline_cache.build_for(conn, "pat", KEYS["pat"], time.time())
    keys = {i["key"] for i in items}
    assert "locations" in keys and "shopping" in keys
    assert "bible" not in keys  # a route that failed is left out, never invented
    assert {m for m, _ in routes} == {"pat"}  # every source asked as Pat
    loc = next(i for i in items if i["key"] == "locations")
    inner, data = open_item(loc, "pat")
    assert data == {"locations": [{"user": "pat", "lat": 1.0}]} and inner["to"] == "pat"
    _, alex_seal = offline_relay.derive_keys(KEYS["alex"])
    assert offline_relay.open_sealed(alex_seal, loc["env"]) is None
    shop = next(i for i in items if i["key"] == "shopping")
    assert open_item(shop, "pat")[1] == {"text": "- [ ] milk"}


@pytest.mark.asyncio
async def test_the_version_changes_only_when_the_content_does(routes):
    conn = FakeRedis()
    first = {i["key"]: i["version"] for i in await offline_cache.build_for(conn, "pat", KEYS["pat"], 1000.0)}
    again = {i["key"]: i["version"] for i in await offline_cache.build_for(conn, "pat", KEYS["pat"], 2000.0)}
    assert first == again
    assert await offline_cache.version_of(conn, "pat", "locations", "other-digest", 3_000_000) == 3_000_000


def test_items_too_big_to_carry_are_left_out():
    assert offline_cache.pack({"a": "x" * 100}) is not None
    assert offline_cache.pack({"a": base64.b64encode(os.urandom(20_000)).decode()}) is None


def test_trips_are_trimmed_to_what_an_offline_screen_shows():
    data = {"trips": [{"id": "t", "user_id": "pat", "route": [1] * 1000, "together": [{"user_id": "alex"}]}]}
    assert offline_cache.trim("trips", data) == {"trips": [{
        "id": "t", "user_id": "pat", "user_name": None, "start_time": None, "end_time": None, "start_location": None,
        "end_location": None, "vehicle_name": None, "vehicle_source": None, "together": [{"user_id": "alex"}],
        "distance_miles": None}]}


# -- shopping edits ----------------------------------------------------------


@pytest.fixture
def note(monkeypatch):
    state = {"text": "- [ ] Milk\n- [x] eggs\n"}
    writes: list[tuple] = []

    async def _call(method, path, api_key, body):
        if path.endswith("/read"):
            return 200, {"status": "SUCCESS", "message": state["text"]}
        writes.append((path, body))
        if path.endswith("/append"):
            state["text"] += body["content"] + "\n"
        if path.endswith("/check_off"):
            state["text"] = state["text"].replace(f"- [ ] {body['item']}", f"- [x] {body['item']}")
        return 200, {"status": "SUCCESS"}

    monkeypatch.setattr(offline_relay, "_call_self", _call)
    return state, writes


@pytest.mark.asyncio
async def test_shopping_edits_are_applied_once(note):
    state, writes = note
    assert (await offline_relay.shopping_op("k", "shopping_add", "Bread"))["ok"]
    assert (await offline_relay.shopping_op("k", "shopping_add", " bread "))["text"] == "bread was already on the list."
    assert (await offline_relay.shopping_op("k", "shopping_check", "Milk"))["ok"]
    assert (await offline_relay.shopping_op("k", "shopping_check", "milk"))["text"] == "milk was already ticked."
    assert (await offline_relay.shopping_op("k", "shopping_check", "Cheese"))["ok"] is False
    assert [p for p, _ in writes] == ["/api/communication/notes/append", "/api/communication/notes/check_off"]
    assert offline_relay.shopping_state(state["text"]) == {"milk": True, "eggs": True, "bread": False}
