"""The home device index (``home_devices``): hubs kept apart, the rename, references and the staleness sweep.

Each hub's rows are its own: a HomeHub sync never removes a Home Assistant row
and the other way round. Rows stored under the old ``ha_entities`` name are
moved once at startup, keeping their vectors. A search hit records the row as
used, and the sweep removes rows nobody refreshes or uses any more, and rows of
a hub that is switched off.
"""

import asyncio
import json
import time

import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import NumpyVecAdapter

DIM = 16


def _fake_embed(texts):
    return [[1.0] + [0.0] * (DIM - 1) for _ in texts]


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(c, DIM)
    monkeypatch.setattr(rag_main, "conn", c)
    monkeypatch.setattr(rag_main, "adapter", NumpyVecAdapter(c))
    monkeypatch.setattr(rag_main, "embedder", object())
    monkeypatch.setattr(rag_main, "embed", _fake_embed)
    monkeypatch.setattr(rag_main, "EMBEDDING_DIM", DIM)
    monkeypatch.setattr(rag_main, "_redis", lambda: None)
    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr("services.config.HA_URL", "")
    rag_main._pending_device_refs.clear()
    yield c
    rag_main._pending_device_refs.clear()


def _entity(eid, name="Lamp", **attrs):
    return {"entity_id": eid, "attributes": {"friendly_name": name, **attrs}}


def _sync(payload, user_id="default"):
    return asyncio.run(rag_main.sync_devices(payload, user_id=user_id))


def _ids(c):
    return {r[0] for r in c.execute("SELECT id FROM rag_items WHERE collection_name = 'home_devices'")}


def _iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


# --- Hubs kept apart -----------------------------------------------------------


def test_each_hub_keeps_its_own_rows(conn):
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _sync({"entities": [_entity("light.porch"), _entity("light.den")], "source": "home_assistant"}, user_id="sam")

    assert {
        "dev:homehub:sam:light.porch",
        "dev:home_assistant:sam:light.porch",
        "dev:home_assistant:sam:light.den",
    } <= _ids(conn)
    meta = json.loads(conn.execute(
        "SELECT metadata FROM rag_items WHERE id = 'dev:home_assistant:sam:light.den'"
    ).fetchone()[0])
    assert meta["source"] == "home_assistant"
    assert meta["type"] == "home_device"


def test_a_sync_from_one_hub_never_removes_the_other_hubs_rows(conn):
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _sync({"entities": [_entity("light.den")], "source": "home_assistant"}, user_id="sam")

    result = _sync({"entities": [_entity("light.attic")], "source": "homehub"}, user_id="sam")

    ids = _ids(conn)
    assert "dev:homehub:sam:light.porch" not in ids
    assert "dev:home_assistant:sam:light.den" in ids
    assert result["orphaned_entity_ids"] == ["light.porch"]


def test_a_sync_without_a_source_is_from_homehub_while_it_is_on(conn):
    result = _sync({"entities": [_entity("light.porch")]}, user_id="sam")
    assert result["source"] == "homehub"
    assert "dev:homehub:sam:light.porch" in _ids(conn)


def test_a_sync_without_a_source_is_from_home_assistant_when_only_it_is_on(conn, monkeypatch):
    monkeypatch.setattr("services.config.HOMEHUB_URL", "")
    monkeypatch.setattr("services.config.HA_URL", "http://ha.example.test:8123")
    assert _sync({"entities": [_entity("light.porch")]}, user_id="sam")["source"] == "home_assistant"


def test_an_unknown_source_is_refused(conn):
    response = _sync({"entities": [_entity("light.porch")], "source": "zigbee"})
    assert response.status_code == 400
    assert not _ids(conn)


def test_the_old_route_name_is_the_same_handler():
    assert rag_main.sync_ha is rag_main.sync_devices
    paths = {getattr(r, "path", "") for r in rag_main.app.routes}
    assert {"/rag/sync/devices", "/rag/sync/ha", "/rag/devices/sweep"} <= paths


def test_sync_status_is_kept_per_hub_and_the_latest_is_reported(conn):
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _sync({"entities": [_entity("light.den"), _entity("light.hall")], "source": "home_assistant"}, user_id="sam")

    status = asyncio.run(rag_main.get_device_sync_status(user_id="sam"))
    assert status["status"] == "SUCCESS"
    assert set(status["sources"]) == {"homehub", "home_assistant"}
    assert status["sources"]["home_assistant"]["count"] == 2


# --- Areas -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attrs", "area"),
    [
        ({"area_id": "Kitchen"}, "Kitchen"),
        ({"area_name": "Den"}, "Den"),
        ({"area": "Loft"}, "Loft"),
        ({}, "unassigned area"),
    ],
)
def test_the_area_is_read_from_what_the_hub_reports(attrs, area):
    assert rag_main._entity_area(attrs) == area


# --- The rename ------------------------------------------------------------------


def _legacy(conn, doc_id, user, content, meta, vector=None):
    rag_main._add_item(
        "ha_entities", doc_id, user, content, meta, 0, _iso(0),
        vector=vector or _fake_embed([content])[0],
    )


def test_legacy_rows_move_to_home_devices_with_their_vectors(conn):
    _legacy(conn, "ha:sam:light.porch", "sam", "Device: Lamp (ID: light.porch) | Area: Porch | Type: light",
            {"entity_id": "light.porch", "type": "ha_entity"}, vector=[0.5] * DIM)
    _legacy(conn, "sync_status:sam", "sam", "Last HA sync", {"type": "sync_status", "timestamp": 1})
    _legacy(conn, "ha:light.porch", "sam", "oldest form", {"entity_id": "light.porch"})
    conn.execute("UPDATE rag_items SET usage_count = 3 WHERE id = 'ha:sam:light.porch'")
    conn.commit()

    assert rag_main._migrate_legacy_device_rows() == {"moved": 2, "deleted": 1}

    assert _ids(conn) == {"dev:homehub:sam:light.porch", "sync_status:homehub:sam"}
    assert not conn.execute("SELECT 1 FROM rag_items WHERE collection_name = 'ha_entities'").fetchone()
    row = conn.execute(
        "SELECT metadata, usage_count FROM rag_items WHERE id = 'dev:homehub:sam:light.porch'"
    ).fetchone()
    meta = json.loads(row["metadata"])
    assert (meta["source"], meta["type"], row["usage_count"]) == ("homehub", "home_device", 3)
    vec = conn.execute(
        "SELECT collection_name FROM vec_store WHERE id = 'dev:homehub:sam:light.porch'"
    ).fetchone()
    assert vec["collection_name"] == "home_devices"
    assert conn.execute("SELECT COUNT(*) FROM vec_store").fetchone()[0] == 2
    fts = conn.execute("SELECT collection_name FROM rag_fts WHERE id = 'dev:homehub:sam:light.porch'").fetchone()
    assert fts is None or fts["collection_name"] == "home_devices"

    # Running it again finds nothing to do.
    assert rag_main._migrate_legacy_device_rows() == {"moved": 0, "deleted": 0}


def test_a_moved_row_is_not_re_embedded_by_the_next_sync(conn):
    content = "Device: Lamp (ID: light.porch) | Area: Porch | Type: light"
    _legacy(conn, "ha:sam:light.porch", "sam", content, {"entity_id": "light.porch"})
    rag_main._migrate_legacy_device_rows()
    embedded = []
    rag_main.embed = lambda texts: embedded.extend(texts) or _fake_embed(texts)

    _sync({"entities": [_entity("light.porch", area_id="Porch")], "source": "homehub"}, user_id="sam")

    assert len(embedded) == 1 and embedded[0].startswith("Last home hub sync")


def test_the_old_name_still_finds_the_devices(conn):
    _sync({"entities": [_entity("light.porch", "Porch Lamp")], "source": "homehub"}, user_id="sam")
    request = SearchRequest(query="porch lamp", user_id="sam", collection_name="ha_entities")
    assert request.collection_name == "home_devices"
    results = asyncio.run(rag_main.search(request)).results
    assert any("light.porch" in r.content for r in results)
    assert IngestRequest(user_id="sam", content="x", collection_name="ha_entities").collection_name == "home_devices"


# --- References ------------------------------------------------------------------


def test_a_search_hit_is_recorded_as_used_when_flushed(conn):
    _sync({"entities": [_entity("light.porch", "Porch Lamp")], "source": "homehub"}, user_id="sam")
    asyncio.run(rag_main.search(SearchRequest(query="porch lamp", user_id="sam", collection_name="home_devices")))

    row = conn.execute("SELECT last_used_at FROM rag_items WHERE id = 'dev:homehub:sam:light.porch'").fetchone()
    assert row["last_used_at"] is None, "a search does not write; the maintenance loop does"
    assert "dev:homehub:sam:light.porch" in rag_main._pending_device_refs

    assert rag_main._flush_device_references() >= 1
    row = conn.execute(
        "SELECT last_used_at, usage_count FROM rag_items WHERE id = 'dev:homehub:sam:light.porch'"
    ).fetchone()
    assert row["last_used_at"] and row["usage_count"] == 1
    assert not rag_main._pending_device_refs


def test_other_collections_are_not_recorded(conn):
    asyncio.run(rag_main.ingest(IngestRequest(user_id="sam", content="porch notes", collection_name="nextcloud_files")))
    asyncio.run(rag_main.search(SearchRequest(query="porch notes", user_id="sam", collection_name="nextcloud_files")))
    assert not rag_main._pending_device_refs


# --- The staleness sweep ----------------------------------------------------------


def _age(conn, doc_id, synced_ago, used_ago=None):
    conn.execute(
        "UPDATE rag_items SET indexed_at = ?, last_used_at = ? WHERE id = ?",
        [_iso(synced_ago), _iso(used_ago) if used_ago is not None else None, doc_id],
    )
    conn.commit()


def test_rows_not_synced_or_used_within_the_age_are_swept(conn, monkeypatch):
    monkeypatch.setattr("services.config.HOME_DEVICE_STALE_HOURS", 72)
    day = 86400
    _sync({"entities": [_entity("light.porch"), _entity("light.den"), _entity("light.hall")],
           "source": "homehub"}, user_id="sam")
    _age(conn, "dev:homehub:sam:light.porch", synced_ago=4 * day)               # stale, never used
    _age(conn, "dev:homehub:sam:light.den", synced_ago=4 * day, used_ago=day)   # stale sync, used lately
    _age(conn, "dev:homehub:sam:light.hall", synced_ago=4 * day, used_ago=5 * day)  # stale and used long ago

    result = rag_main._sweep_stale_devices()

    ids = _ids(conn)
    assert "dev:homehub:sam:light.porch" not in ids
    assert "dev:homehub:sam:light.hall" not in ids
    assert "dev:homehub:sam:light.den" in ids
    assert "sync_status:homehub:sam" in ids, "the user still has a row from this hub"
    assert result["removed_stale"] == 2
    assert result["removed_entity_ids"] == ["light.hall", "light.porch"]


def test_a_user_whose_sync_stopped_loses_every_row_and_the_status(conn, monkeypatch):
    monkeypatch.setattr("services.config.HOME_DEVICE_STALE_HOURS", 72)
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="alex")
    _age(conn, "dev:homehub:sam:light.porch", synced_ago=100 * 3600)

    result = rag_main._sweep_stale_devices()

    ids = _ids(conn)
    assert not {i for i in ids if ":sam" in i}
    assert {"dev:homehub:alex:light.porch", "sync_status:homehub:alex"} <= ids
    assert result["removed_sync_status"] == 1
    # Another user still has the entity, so its cached state stays.
    assert result["removed_entity_ids"] == []


def test_a_fresh_row_is_kept(conn):
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    result = rag_main._sweep_stale_devices()
    assert "dev:homehub:sam:light.porch" in _ids(conn)
    assert result["removed_stale"] == result["removed_switched_off"] == 0


def test_rows_of_a_switched_off_hub_are_swept(conn):
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _sync({"entities": [_entity("light.den")], "source": "home_assistant"}, user_id="sam")

    result = rag_main._sweep_stale_devices()  # HA_URL is blank in the fixture: Home Assistant is off

    ids = _ids(conn)
    assert "dev:home_assistant:sam:light.den" not in ids
    assert "sync_status:home_assistant:sam" not in ids
    assert "dev:homehub:sam:light.porch" in ids
    assert result["removed_switched_off"] == 1


def test_the_sweep_clears_the_state_cache_of_entities_no_row_holds(conn, monkeypatch):
    deleted = []

    class _Cache:
        def delete(self, *keys):
            deleted.extend(keys)

    monkeypatch.setattr(rag_main, "_redis", lambda: _Cache())
    _sync({"entities": [_entity("light.den")], "source": "home_assistant"}, user_id="sam")
    rag_main._sweep_stale_devices()
    assert deleted == ["ha:state:light.den"]


def test_the_sweep_writes_pending_references_first(conn, monkeypatch):
    monkeypatch.setattr("services.config.HOME_DEVICE_STALE_HOURS", 72)
    _sync({"entities": [_entity("light.porch")], "source": "homehub"}, user_id="sam")
    _age(conn, "dev:homehub:sam:light.porch", synced_ago=100 * 3600)
    rag_main._note_device_references(["dev:homehub:sam:light.porch"])

    rag_main._sweep_stale_devices()

    assert "dev:homehub:sam:light.porch" in _ids(conn)


def test_the_age_is_a_startup_setting_with_a_default():
    from services import config as cfg

    assert cfg.HOME_DEVICE_STALE_HOURS == 72
    assert cfg.HOME_DEVICE_SWEEP_MINUTES == 60


def test_stored_lessons_are_taught_the_new_name(conn):
    rag_main._add_item(
        "system_learnings", "lesson-x", "default", "Search ha_entities for the house",
        {"rule": "Search ha_entities for the house"}, 0, _iso(0),
    )
    conn.execute("UPDATE rag_items SET applied_count = 2 WHERE id = 'lesson-x'")
    conn.commit()

    assert rag_main._rename_legacy_collection_in_text() == 1

    row = conn.execute("SELECT content, rule, metadata, applied_count FROM rag_items WHERE id = 'lesson-x'").fetchone()
    assert "home_devices" in row["content"] and "ha_entities" not in row["content"]
    assert "ha_entities" not in row["rule"] + row["metadata"]
    assert row["applied_count"] == 2
    assert rag_main._rename_legacy_collection_in_text() == 0
