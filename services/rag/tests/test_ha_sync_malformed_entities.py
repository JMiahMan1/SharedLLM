"""Refusal of entity ids that are not ``domain.object_id``.

The live ``ha_entities`` index carried two entries whose id was a serialized
user record about 1,100 characters long (display name, Nextcloud/GitHub URLs,
e-mail addresses) -- Home Assistant accepted it because anything can be
registered, and this service indexed it verbatim. Those two entries also set
the padded batch length for every text they travelled with, which is what
drove one embed call from 1,905 MiB to 5,654 MiB and OOM-killed the
container. These tests pin that the id is refused at the boundary, reported
in the response, warned about in the log, and that a copy stored by an
earlier sync is removed as an orphan on the next one.
"""

import asyncio

from services.rag import db
from services.rag import main as rag_main
from services.rag.store import NumpyVecAdapter

DIM = 16
POISON_ID = "media_player." + ("id_1_username_default_display_name_" * 34)


def _fake_embed(texts):
    return [[0.0] * DIM for _ in texts]


def _install(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()
    rag_main.embed = _fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


def _sync(payload, user_id="default"):
    return asyncio.run(rag_main.sync_ha(payload, user_id=user_id))


def _valid(i=0):
    return {
        "entity_id": f"light.kitchen_{i}",
        "attributes": {"friendly_name": f"Kitchen Light {i}"},
    }


def _stored_ids(conn):
    rows = conn.execute(
        "SELECT id FROM rag_items WHERE collection_name = 'ha_entities'"
    ).fetchall()
    return {r["id"] for r in rows}


def test_the_poisoned_id_is_refused_and_reported(tmp_path, caplog):
    import logging

    conn = _install(tmp_path)
    caplog.set_level(logging.WARNING)
    result = _sync({"entities": [_valid(0), _valid(1), {"entity_id": POISON_ID}]})

    assert result["status"] == "SUCCESS"
    assert result["count"] == 2
    assert result["refused_count"] == 1
    assert f"ha:{POISON_ID}" not in _stored_ids(conn)
    assert any("malformed entity_ids" in r.message for r in caplog.records)


def test_valid_entities_still_index_normally_alongside_a_refusal(tmp_path):
    conn = _install(tmp_path)
    _sync({"entities": [_valid(0), {"entity_id": POISON_ID}, _valid(1)]})
    assert {"ha:light.kitchen_0", "ha:light.kitchen_1"} <= _stored_ids(conn)


def test_a_previously_stored_poisoned_row_is_removed_as_an_orphan(tmp_path):
    conn = _install(tmp_path)
    rag_main._add_item(
        "ha_entities",
        f"ha:{POISON_ID}",
        "default",
        "Device: {\"username\": \"default\"}",
        {"entity_id": POISON_ID, "type": "ha_entity"},
        0,
        "2026-01-01T00:00:00Z",
        vector=[0.0] * DIM,
    )
    assert f"ha:{POISON_ID}" in _stored_ids(conn)

    result = _sync({"entities": [_valid(0)]})

    assert f"ha:{POISON_ID}" not in _stored_ids(conn)
    assert f"ha:{POISON_ID}" in result["orphaned_entity_ids"]


def test_a_long_but_legitimate_entity_id_is_kept(tmp_path):
    conn = _install(tmp_path)
    long_id = "sensor." + "a" * 190
    assert len(long_id) <= rag_main._ENTITY_ID_MAX_LENGTH

    result = _sync({"entities": [{"entity_id": long_id, "attributes": {}}]})

    assert result["refused_count"] == 0
    assert f"ha:{long_id}" in _stored_ids(conn)


def test_a_entries_without_an_entity_id_are_still_skipped_quietly(tmp_path, caplog):
    import logging

    _install(tmp_path)
    caplog.set_level(logging.WARNING)
    result = _sync({"entities": [{"attributes": {"friendly_name": "no id"}}, _valid(0)]})

    assert result["count"] == 1
    assert result["refused_count"] == 0
    assert not [r for r in caplog.records if "malformed entity_ids" in r.message]


def test_entities_that_are_not_a_list_are_refused(tmp_path):
    _install(tmp_path)
    response = asyncio.run(rag_main.sync_ha({"entities": "light.kitchen_0"}))
    assert response.status_code == 400
    assert b"entities must be a list" in response.body


def test_the_shape_check_rejects_the_shapes_that_matter():
    assert rag_main._usable_entity_id("light.kitchen")
    assert rag_main._usable_entity_id("automation." + "b" * 200)
    assert not rag_main._usable_entity_id(POISON_ID), "length is the discriminator"
    assert not rag_main._usable_entity_id("")
    assert not rag_main._usable_entity_id(None)
    assert not rag_main._usable_entity_id("nodomain")
    assert not rag_main._usable_entity_id("a.b.c")
    assert not rag_main._usable_entity_id("has space.entity")
    assert not rag_main._usable_entity_id("Light.kitchen")
