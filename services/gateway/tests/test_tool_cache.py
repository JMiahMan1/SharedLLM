"""The tool-result cache: declared freshness, per-user and per-conversation scope,
invalidation, the timestamp a reused result carries, learning, and warm refresh."""
from __future__ import annotations

import json

import pytest

from services.gateway import tool_cache as tc
from services.gateway import tool_registry as tr


class _Source:
    """A tool backend that counts its calls and can change its answer."""

    def __init__(self, answer="ANSWER"):
        self.answer = answer
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.answer


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(tc, "_now", lambda: now["t"])
    return now


async def _call(tool, payload, source, *, user="alice", conversation="c1"):
    return await tc.call(tool, payload, user=user, fetch=source, conversation=conversation)


# --- classes -------------------------------------------------------------------------------

@pytest.mark.parametrize("tool, payload", [
    ("EntitySearchRequest", {"query": "porch light"}),
    ("LightControlRequest", {"entity_id": "light.x", "action": "turn_on"}),
    ("SecurityRequest", {"action": "status"}),
    ("LocationRequest", {"user": "me"}),
    ("TimerRequest", {"action": "list"}),
    ("MediaStatusRequest", {}),
    ("ContextSearchRequest", {"collection_name": "home_devices", "query": "lights"}),
    ("ContextSearchRequest", {"collection_name": "telemetry_alerts", "query": "x"}),
    ("SomeToolNobodyDeclared", {"q": 1}),
    ("BibleRequest", {"action": "something_new"}),
])
async def test_live_and_unknown_tools_always_go_to_the_source(tool, payload, clock):
    source = _Source()
    for _ in range(3):
        assert await _call(tool, payload, source) == "ANSWER"
    assert source.calls == 3


async def test_a_live_tool_is_never_promoted_even_with_an_override(clock, _tool_cache_store):
    _tool_cache_store.hset(f"{tc.PREFIX}:overrides", "EntitySearchRequest", "static")
    source = _Source()
    await _call("EntitySearchRequest", {"query": "x"}, source)
    await _call("EntitySearchRequest", {"query": "x"}, source)
    assert source.calls == 2


async def test_a_short_result_is_reused_then_expires(clock):
    source = _Source("3 events tomorrow")
    first = await _call("CalendarRequest", {"action": "read"}, source)
    clock["t"] += 30
    again = await _call("CalendarRequest", {"action": "read"}, source)
    assert first == "3 events tomorrow" and source.calls == 1
    assert again.endswith("3 events tomorrow") and again.startswith("[Saved result from")
    clock["t"] += tc.SHORT_S + 1
    await _call("CalendarRequest", {"action": "read"}, source)
    assert source.calls == 2


async def test_daily_data_lasts_until_midnight(clock):
    assert 60 <= tc.ttl_for(tr.DAILY, clock["t"]) <= 86400
    source = _Source("John 3:16")
    await _call("BibleRequest", {"action": "votd"}, source)
    clock["t"] += 50
    await _call("BibleRequest", {"action": "votd"}, source)
    assert source.calls == 1


async def test_static_data_outlives_a_short_one(clock):
    assert tc.ttl_for(tr.STATIC) > tc.ttl_for(tr.SHORT)
    source = _Source("In the beginning")
    await _call("BibleRequest", {"action": "read", "ref": "Genesis 1:1"}, source)
    clock["t"] += 3600
    await _call("BibleRequest", {"action": "read", "ref": "genesis  1:1"}, source)  # same args, other spelling
    assert source.calls == 1


async def test_a_reused_result_says_when_it_was_fetched(clock):
    source = _Source("Mow the lawn")
    await _call("ChoreRequest", {"action": "list"}, source)
    clock["t"] += 90
    text = await _call("ChoreRequest", {"action": "list"}, source)
    assert "(90 s ago, short data)" in text
    assert "fetch again if it must be current" in text


async def test_a_failed_result_is_never_kept(clock):
    source = _Source("Sorry, I couldn't complete that action: calendar is down")
    await _call("CalendarRequest", {"action": "read"}, source)
    await _call("CalendarRequest", {"action": "read"}, source)
    assert source.calls == 2


# --- scope -----------------------------------------------------------------------------------

async def test_one_users_result_never_reaches_another(clock):
    source = _Source("alice's calendar")
    await _call("CalendarRequest", {"action": "read"}, source, user="alice")
    await _call("CalendarRequest", {"action": "read"}, source, user="bob")
    assert source.calls == 2


async def test_a_new_conversation_does_not_reuse_anothers_result(clock):
    source = _Source("calendar")
    await _call("CalendarRequest", {"action": "read"}, source, conversation="kitchen")
    await _call("CalendarRequest", {"action": "read"}, source, conversation="den")
    assert source.calls == 2


async def test_household_public_data_is_shared_across_people(clock):
    source = _Source("Psalm 23")
    await _call("BibleRequest", {"action": "votd"}, source, user="alice", conversation="a")
    await _call("BibleRequest", {"action": "votd"}, source, user="bob", conversation="b")
    assert source.calls == 1


async def test_per_user_entries_are_bounded(clock, monkeypatch, _tool_cache_store):
    monkeypatch.setattr(tc, "MAX_ENTRIES_PER_USER", 3)
    for i in range(6):
        clock["t"] += 1
        await _call("WebSearchRequest", {"query": f"q{i}"}, _Source(f"r{i}"))
    index = f"{tc.PREFIX}:index:{tc._user_key('alice')}"
    assert _tool_cache_store.zcard(index) == 3
    kept = [k for k in _tool_cache_store.scan_iter(f"{tc.PREFIX}:conv:*")]
    assert len(kept) == 3


# --- invalidation -----------------------------------------------------------------------------

async def test_a_write_drops_the_reads_it_makes_stale(clock):
    source = _Source("basil needs water")
    await _call("garden_summary", {}, source)
    await _call("garden_log_care", {"target": "basil", "action": "water"}, _Source("Watered."))
    await _call("garden_summary", {}, source)
    assert source.calls == 2


async def test_a_write_action_of_a_cached_tool_is_live_and_invalidates_its_reads(clock):
    reads = _Source("1 chore")
    await _call("ChoreRequest", {"action": "list"}, reads)
    write = _Source("Added.")
    await _call("ChoreRequest", {"action": "add", "title": "feed the dog"}, write)
    await _call("ChoreRequest", {"action": "add", "title": "feed the dog"}, write)
    assert write.calls == 2
    await _call("ChoreRequest", {"action": "list"}, reads)
    assert reads.calls == 2


async def test_an_event_drops_a_tag(clock):
    source = _Source("lesson: run tests before pushing")
    payload = {"collection_name": "system_learnings", "query": "push"}
    await _call("ContextSearchRequest", payload, source)
    assert tc.invalidate_tag("lessons", reason="reindex") == 1
    await _call("ContextSearchRequest", payload, source)
    assert source.calls == 2


async def test_dropping_a_user_drops_only_their_entries(clock):
    a, b = _Source("a"), _Source("b")
    await _call("CalendarRequest", {"action": "read"}, a, user="alice")
    await _call("CalendarRequest", {"action": "read"}, b, user="bob")
    assert tc.invalidate_user("alice") == 1
    await _call("CalendarRequest", {"action": "read"}, a, user="alice")
    await _call("CalendarRequest", {"action": "read"}, b, user="bob")
    assert (a.calls, b.calls) == (2, 1)


# --- a cache that is down is a miss, never a failure ----------------------------------------------

class _Broken:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("redis is down")
        return fail


async def test_without_redis_every_call_goes_to_the_source(clock):
    tc.set_client(_Broken())
    source = _Source("fine")
    assert await _call("CalendarRequest", {"action": "read"}, source) == "fine"
    assert await _call("CalendarRequest", {"action": "read"}, source) == "fine"
    assert source.calls == 2


class _Counting:
    """A Redis that fails, and counts how often it is asked."""

    def __init__(self):
        self.asked = 0

    def __getattr__(self, name):
        def fail(*a, **k):
            self.asked += 1
            raise ConnectionError("redis is down")
        return fail


async def test_an_outage_costs_one_failed_round_trip_not_one_per_call(clock):
    store = _Counting()
    tc.set_client(store)
    for _ in range(5):
        await _call("CalendarRequest", {"action": "read"}, _Source("x"))
    assert store.asked == 1


async def test_a_live_call_never_touches_redis(clock):
    store = _Counting()
    tc.set_client(store)
    for _ in range(3):
        await _call("LightControlRequest", {"entity_id": "light.x", "action": "turn_on"}, _Source("On."))
    assert store.asked == 0


async def test_a_write_during_an_outage_invalidates_once_redis_is_back(clock, monkeypatch):
    import fakeredis

    store = fakeredis.FakeRedis(decode_responses=True)
    tc.set_client(store)
    reads = _Source("lesson v1")
    payload = {"collection_name": "system_learnings", "query": "push"}
    await _call("ContextSearchRequest", payload, reads)
    # Redis goes away; a lesson is written; Redis comes back.
    monkeypatch.setattr(tc, "_client_override", _Broken())
    await _call("SystemLearningRequest", {"action": "ingest", "rule": "new"}, _Source("Saved."))
    assert "lessons" in tc._pending_invalidations
    monkeypatch.setattr(tc, "_client_override", store)
    monkeypatch.setattr(tc, "_down_until", 0.0)
    reads.answer = "lesson v2"
    assert await _call("ContextSearchRequest", payload, reads) == "lesson v2"
    assert not tc._pending_invalidations


# --- learning -------------------------------------------------------------------------------

def _seed_stats(store, tool, same, changed):
    store.hset(f"{tc.PREFIX}:stats:{tool}", mapping={"same": same, "changed": changed})


async def test_refetches_are_compared_with_the_last_value(clock, _tool_cache_store):
    source = _Source("same answer")
    await _call("WebSearchRequest", {"query": "x"}, source)
    clock["t"] += tc.SHORT_S + 1
    await _call("WebSearchRequest", {"query": "x"}, source)
    source.answer = "a new answer"
    clock["t"] += tc.SHORT_S + 1
    await _call("WebSearchRequest", {"query": "x"}, source)
    stats = _tool_cache_store.hgetall(f"{tc.PREFIX}:stats:WebSearchRequest")
    assert stats["same"] == "1" and stats["changed"] == "1"
    assert stats["misses"] == "3"


def test_a_tool_that_never_changes_is_promoted_within_the_bound(_tool_cache_store, monkeypatch):
    monkeypatch.setattr(tc, "MAX_CLASS", tr.DAILY)
    _seed_stats(_tool_cache_store, "WebSearchRequest", same=40, changed=0)
    [s] = [x for x in tc.tune(apply=True) if x["tool"] == "WebSearchRequest"]
    assert (s["from"], s["to"], s["applied"]) == ("short", "daily", True)
    assert _tool_cache_store.hget(f"{tc.PREFIX}:overrides", "WebSearchRequest") == "daily"
    # and no further than the bound, however much more evidence arrives
    _seed_stats(_tool_cache_store, "WebSearchRequest", same=400, changed=0)
    assert not [x for x in tc.suggest() if x["tool"] == "WebSearchRequest"]


def test_a_tool_whose_value_keeps_changing_is_demoted_but_not_to_live(_tool_cache_store):
    _tool_cache_store.hset(f"{tc.PREFIX}:overrides", "WebReadRequest", "daily")
    _seed_stats(_tool_cache_store, "WebReadRequest", same=5, changed=30)
    [s] = [x for x in tc.suggest() if x["tool"] == "WebReadRequest"]
    assert (s["from"], s["to"]) == ("daily", "short")
    _tool_cache_store.hset(f"{tc.PREFIX}:overrides", "WebReadRequest", "short")
    assert not [x for x in tc.suggest() if x["tool"] == "WebReadRequest"]


def test_too_little_evidence_changes_nothing(_tool_cache_store):
    _seed_stats(_tool_cache_store, "WebSearchRequest", same=tc.TUNE_MIN_SAMPLES - 1, changed=0)
    assert not tc.suggest()


def test_suggestions_are_only_suggestions_until_applied(_tool_cache_store):
    _seed_stats(_tool_cache_store, "WebSearchRequest", same=40, changed=0)
    assert tc.tune(apply=False)
    assert _tool_cache_store.hget(f"{tc.PREFIX}:overrides", "WebSearchRequest") is None


def test_no_live_tool_is_ever_suggested(_tool_cache_store):
    for tool in ("EntitySearchRequest", "LocationRequest", "TimerRequest", "SecurityRequest"):
        _seed_stats(_tool_cache_store, tool, same=1000, changed=0)
    assert not [s for s in tc.suggest() if s["tool"] in
                ("EntitySearchRequest", "LocationRequest", "TimerRequest", "SecurityRequest")]


# --- warm refresh ---------------------------------------------------------------------------

async def test_a_hot_public_entry_is_refreshed_before_it_expires(clock, _tool_cache_store):
    source = _Source("Psalm 23")
    for _ in range(1 + tc.HOT_USES):
        await _call("BibleRequest", {"action": "votd"}, source)
    assert source.calls == 1
    [key] = list(_tool_cache_store.scan_iter(f"{tc.PREFIX}:pub:*"))
    entry = json.loads(_tool_cache_store.get(key))
    clock["t"] = entry["expires_at"] - 30  # inside the last fifth of its life
    seen = []

    async def fetch(tool, payload):
        seen.append((tool, payload))
        return "Psalm 23"

    assert await tc.refresh_hot(fetch) == 1
    assert seen == [("BibleRequest", {"action": "votd"})]
    assert json.loads(_tool_cache_store.get(key))["fetched_at"] == clock["t"]


async def test_per_user_entries_are_never_refreshed_in_the_background(clock, _tool_cache_store):
    source = _Source("alice's notes")
    for _ in range(5):
        await _call("NoteRequest", {"action": "list"}, source)
    clock["t"] += tc.SHORT_S - 5

    async def fetch(tool, payload):
        raise AssertionError("acted as a person who is not asking")

    assert await tc.refresh_hot(fetch) == 0


async def test_the_report_shows_hit_rate_and_time_saved(clock):
    source = _Source("Psalm 23")
    for _ in range(4):
        await _call("BibleRequest", {"action": "votd"}, source)
    report = tc.report()
    bible = report["tools"]["BibleRequest"]
    assert bible["hits"] == 3 and bible["misses"] == 1 and bible["hit_rate"] == 0.75
    assert report["declared"]["BibleRequest"]["household_public"] is True


# --- the chat path uses it ---------------------------------------------------------------------

async def test_the_single_turn_executor_reuses_a_result_within_the_conversation(monkeypatch):
    from unittest.mock import AsyncMock

    from services.gateway import orchestrator

    run = AsyncMock(return_value="VOTD: Psalm 23")
    monkeypatch.setattr(orchestrator, "_run_single_tool", run)
    creds = orchestrator.ResolvedCredentials(user="alice")
    call = {"action": "biblerequest", "payload": {"action": "votd"}}
    first = await orchestrator._execute_single_tool("biblerequest", dict(call), "q", creds)
    second = await orchestrator._execute_single_tool("biblerequest", dict(call), "q", creds)
    assert first == "VOTD: Psalm 23" and second.endswith("VOTD: Psalm 23")
    assert run.await_count == 1
    light = {"action": "lightcontrolrequest", "payload": {"entity_id": "light.x", "action": "turn_on"}}
    await orchestrator._execute_single_tool("lightcontrolrequest", dict(light), "q", creds)
    await orchestrator._execute_single_tool("lightcontrolrequest", dict(light), "q", creds)
    assert run.await_count == 3


def test_every_cached_tool_is_registered_and_declares_real_classes():
    names = {row[0] for row in tr._RAVEN_TOOL_TABLE} | set(tr.SINGLE_TURN_GARDEN_TOOLS)
    for tool, spec in tr.FRESHNESS.items():
        assert tool in names, tool
        for cls in (spec.default, *spec.reads.values(), *spec.by_collection.values()):
            assert cls in tr.FRESHNESS_CLASSES
    for tool in tr.WRITE_INVALIDATES:
        assert tool in names, tool


def _admin_client(monkeypatch, admin=True):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from services.gateway import main

    async def require_admin(request):
        if not admin:
            raise HTTPException(status_code=403, detail="Admins only")
        return "admin"

    monkeypatch.setattr(main, "_require_admin", require_admin)
    # The routes alone: the gateway's middleware (identity, rate limits) is not what is tested.
    from fastapi import FastAPI

    from services.gateway import context_routes

    app = FastAPI()
    app.include_router(context_routes.router)
    return TestClient(app)


def test_the_cache_endpoints_are_admin_only(monkeypatch):
    c = _admin_client(monkeypatch, admin=False)
    assert c.get("/api/admin/tool-cache").status_code == 403
    assert c.post("/api/admin/tool-cache/invalidate", json={"tag": "files"}).status_code == 403
    assert c.post("/api/admin/tool-cache/tune", json={}).status_code == 403


async def test_the_cache_endpoints_report_invalidate_and_tune(monkeypatch, clock, _tool_cache_store):
    await _call("ContextSearchRequest", {"collection_name": "nextcloud_files", "query": "soup"}, _Source("Soup.md"))
    c = _admin_client(monkeypatch)
    body = c.get("/api/admin/tool-cache").json()
    assert body["tools"]["ContextSearchRequest"]["misses"] == 1
    assert c.post("/api/admin/tool-cache/invalidate", json={"tag": "files"}).json()["dropped"] == 1
    assert c.post("/api/admin/tool-cache/invalidate", json={}).status_code == 422
    _seed_stats(_tool_cache_store, "WebSearchRequest", same=50, changed=0)
    tuned = c.post("/api/admin/tool-cache/tune", json={"apply": True}).json()["suggestions"]
    assert any(s["tool"] == "WebSearchRequest" and s.get("applied") for s in tuned)
    assert c.get("/api/admin/tool-cache").json()["overrides"] == {"WebSearchRequest": "daily"}
    assert c.delete("/api/admin/tool-cache/overrides/WebSearchRequest").json() == {"status": "SUCCESS"}
    assert c.get("/api/admin/tool-cache").json()["overrides"] == {}


def test_safety_and_presence_tools_are_not_declared_cacheable():
    for tool in ("SecurityRequest", "LightControlRequest", "ClimateRequest", "LocationRequest",
                 "TimerRequest", "EntitySearchRequest", "MediaStatusRequest", "LogbookRequest",
                 "TodoListRequest", "HAServiceRequest"):
        assert tool not in tr.FRESHNESS


# --- stats -------------------------------------------------------------------------------------

def test_a_millisecond_field_is_never_written_as_an_integer(_tool_cache_store):
    """The live `ResponseError ... hash value is not an integer` for `source_ms`.

    A timing field is a float, but a float with no fraction (`972.0`) used to be
    written with HINCRBY. Once the field already held `12.5` Redis refused that
    command outright, the exception marked the whole cache down, and every call
    went to its source for 30 s.
    """
    tc._down_until = 0.0
    tc._stat("ContextSearchRequest", misses=1, source_ms=12.5, source_calls=1)
    tc._stat("ContextSearchRequest", misses=1, source_ms=972.0, source_calls=1)
    tc._stat("ContextSearchRequest", hits=1, saved_ms=8.0)
    tc._stat("ContextSearchRequest", hits=1, saved_ms=0.25)

    stats = _tool_cache_store.hgetall(f"{tc.PREFIX}:stats:ContextSearchRequest")
    assert float(stats["source_ms"]) == pytest.approx(984.5)
    assert float(stats["saved_ms"]) == pytest.approx(8.25)
    assert stats["misses"] == "2" and stats["hits"] == "2"
    assert tc._down_until == 0.0, "a refused stat must not take the cache down"
