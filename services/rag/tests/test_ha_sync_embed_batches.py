"""Chunked embedding for the batch endpoints.

This pins the production OOM. ``POST /rag/sync/ha`` embedded all 684 Home
Assistant entity descriptions in ONE ONNX batch, measured on the live index
(nomic-embed v1.5, 61-178 char entity strings) as:

    50 -> 1.1 GiB peak   150 -> 1.7 GiB   300 -> 2.8 GiB
    500 -> 2.9 GiB       664 -> OOM-killed

against a 6 GiB container that also carries ~0.9 GiB of model baseline. The
gateway's cleanup loop calls that endpoint every 300 s per user, so each kill
restarted the service and the next user's sync repeated the batch -- kernel
logs showed OOM kills arriving in pairs roughly every six minutes, with the
gateway logging ``Cleanup: deferred ... Server disconnected`` at the same
second. ``reindex_all`` already batches at 256 for exactly this reason; these
tests pin that every batch endpoint does too.
"""

import asyncio
import inspect
import math
from pathlib import Path

import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.store import NumpyVecAdapter

DIM = 16


def _fake_embed(texts):
    """Record each batch exactly as the ONNX runtime would receive it."""
    recorder = getattr(_fake_embed, "batches", None)
    if recorder is None:
        recorder = []
        _fake_embed.batches = recorder
    recorder.append(list(texts))
    return [[0.0] * DIM for _ in texts]


def _install(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()
    rag_main.embed = _fake_embed
    rag_main.EMBEDDING_DIM = DIM
    _fake_embed.batches = []
    return conn


def _entities(n):
    return [
        {
            "entity_id": f"light.kitchen_{i}",
            "attributes": {"friendly_name": f"Kitchen Light {i}", "area_id": "kitchen"},
        }
        for i in range(n)
    ]


def _sync(payload, user_id="default"):
    return asyncio.run(rag_main.sync_ha(payload, user_id=user_id))


def _recorded():
    return [text for batch in _fake_embed.batches for text in batch]


def test_a_large_sync_embeds_in_bounded_batches(tmp_path):
    _install(tmp_path)
    _sync({"entities": _entities(300), "user_id": "default"})

    batches = _fake_embed.batches
    assert batches, "the sync must embed something"
    assert all(len(b) <= rag_main.EMBED_BATCH_SIZE for b in batches), (
        f"batch sizes {[len(b) for b in batches]} exceed "
        f"EMBED_BATCH_SIZE={rag_main.EMBED_BATCH_SIZE}"
    )
    expected_texts = 300 + 1  # one entity per row plus the sync_status row
    assert sum(len(b) for b in batches) == expected_texts
    assert len(batches) == math.ceil(expected_texts / rag_main.EMBED_BATCH_SIZE)


def test_every_entity_text_is_embedded_once_in_order(tmp_path):
    _install(tmp_path)
    _sync({"entities": _entities(300), "user_id": "default"})

    recorded = _recorded()
    assert len(recorded) == 301
    assert recorded[0].startswith("Device:")
    assert recorded[1].startswith("Device:")
    assert recorded[299].startswith("Device:")
    assert recorded[-1].startswith("Last HA sync for default")


def test_a_small_sync_still_lands_in_one_batch(tmp_path):
    _install(tmp_path)
    _sync({"entities": _entities(5), "user_id": "default"})

    batches = _fake_embed.batches
    assert len(batches) == 1
    assert len(batches[0]) == 6


def test_the_sync_still_reports_success_and_counts(tmp_path):
    _install(tmp_path)
    result = _sync({"entities": _entities(5), "user_id": "default"})

    assert result["status"] == "SUCCESS"
    assert result["count"] == 5
    assert result["new_count"] == 5  # entities only; the status row is appended after counting
    assert result["removed_count"] == 0
    assert result["orphaned_entity_ids"] == []


def test_an_empty_payload_embeds_only_the_status_row(tmp_path):
    _install(tmp_path)
    result = _sync({"entities": [], "user_id": "jeremiah"})

    assert result["status"] == "SUCCESS"
    assert result["count"] == 0
    assert len(_fake_embed.batches) == 1
    assert len(_fake_embed.batches[0]) == 1


def test_an_empty_batch_size_is_refused_rather_than_looping(tmp_path):
    _install(tmp_path)
    with pytest.raises(ValueError, match="batch_size"):
        rag_main.embed_batched(["anything"], batch_size=0)


def test_an_empty_text_list_costs_no_embedding_call(tmp_path):
    _install(tmp_path)
    assert rag_main.embed_batched([]) == []
    assert _fake_embed.batches == []


def test_the_batch_ceiling_stays_inside_the_measured_budget():
    """150 real entity strings already peak near 1.7 GiB, so a batch of
    hundreds re-opens the OOM. The ceiling is a guardrail, not a tuning
    knob -- this asserts it never silently grows past the safe range."""
    assert 1 <= rag_main.EMBED_BATCH_SIZE <= 256


def test_no_batch_endpoint_still_embeds_one_giant_list():
    """Structural pin: every batch endpoint must go through embed_batched.

    `to_thread(embed, [...])` with a caller-supplied list is the exact shape
    that OOM-killed the service; the four callers (dream re-embed, file sync,
    HA sync, capability sync) are allowed to use only the chunked helper.
    """
    source = Path(inspect.getsourcefile(rag_main)).read_text()
    assert "asyncio.to_thread(embed," not in source
    assert source.count("asyncio.to_thread(embed_batched,") == 4


class _CountingConn:
    """The connection, counting commits (sqlite3.Connection.commit is read-only)."""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def commit(self):
        self.commits += 1
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_sync_commits_once_not_once_per_entity(tmp_path):
    """A commit is a disk sync; one per entity kept a 700-entity sync on the
    event loop for 17 s, long enough for every /health probe to time out."""
    conn = _install(tmp_path)
    counting = _CountingConn(conn)
    rag_main.conn = counting
    _sync({"entities": _entities(50), "user_id": "default"})
    assert counting.commits == 1
    rows = conn.execute("SELECT COUNT(*) FROM rag_items WHERE collection_name = 'ha_entities'").fetchone()[0]
    assert rows == 51


def test_a_repeat_sync_does_not_re_embed_unchanged_entities(tmp_path):
    """The gateway re-syncs every 5 min per user; re-embedding ~700 unchanged
    entities each time kept the container at ~350% CPU continuously."""
    conn = _install(tmp_path)
    _sync({"entities": _entities(50), "user_id": "default"})
    _fake_embed.batches = []

    changed = _entities(50)
    changed[3]["attributes"]["friendly_name"] = "Renamed Lamp"
    changed[7]["attributes"]["_device_ip"] = "10.0.0.7"  # metadata-and-content change
    result = _sync({"entities": changed, "user_id": "default"})

    recorded = _recorded()
    assert result["status"] == "SUCCESS"
    assert result["new_count"] == 0
    assert len(recorded) == 3  # the two changed entities plus the status row
    assert any("Renamed Lamp" in t for t in recorded)
    rows = conn.execute(
        "SELECT COUNT(*) FROM rag_items WHERE collection_name = 'ha_entities'"
    ).fetchone()[0]
    assert rows == 51


def test_an_unchanged_entity_still_gets_fresh_metadata_and_keeps_usage(tmp_path):
    import json as _json

    conn = _install(tmp_path)
    _sync({"entities": _entities(2), "user_id": "default"})
    conn.execute("UPDATE rag_items SET usage_count = 4, indexed_at = 'old' WHERE id = 'ha:default:light.kitchen_0'")
    conn.commit()

    again = _entities(2)
    again[0]["attributes"]["_device_discovery_method"] = "mdns"  # not part of the embedded text
    _sync({"entities": again, "user_id": "default"})

    row = conn.execute(
        "SELECT metadata, indexed_at, usage_count FROM rag_items WHERE id = 'ha:default:light.kitchen_0'"
    ).fetchone()
    meta = _json.loads(row["metadata"])
    assert meta["device_discovery_method"] == "mdns"
    assert meta["usage_count"] == 4
    assert row["usage_count"] == 4
    assert row["indexed_at"] != "old"


def test_users_keep_their_own_copies_and_do_not_re_embed_each_other(tmp_path):
    """Live, three users synced the same ~700 entities in turn. The ids were
    shared, so each sync replaced the previous user's rows, found nothing of its
    own unchanged, and re-embedded everything: RAG spent about half of every
    hour embedding, and only the last user to sync had HA entities indexed."""
    conn = _install(tmp_path)
    for user in ("default", "michele", "jeremiah"):
        _sync({"entities": _entities(20)}, user_id=user)
    _fake_embed.batches = []

    for user in ("default", "michele", "jeremiah"):
        _sync({"entities": _entities(20)}, user_id=user)

    # Only the three status rows are re-embedded; every entity is unchanged.
    assert len(_recorded()) == 3
    per_user = dict(
        conn.execute(
            "SELECT user_id, COUNT(*) FROM rag_items WHERE collection_name = 'ha_entities'"
            " AND id LIKE 'ha:%' GROUP BY user_id"
        ).fetchall()
    )
    assert per_user == {"default": 20, "michele": 20, "jeremiah": 20}


def test_rows_under_the_old_shared_id_are_replaced_without_dropping_live_entities(tmp_path):
    conn = _install(tmp_path)
    rag_main._add_item(
        "ha_entities", "ha:light.kitchen_0", "michele", "old text", {"entity_id": "light.kitchen_0"},
        0, "2026-01-01T00:00:00Z", vector=[0.0] * rag_main.EMBEDDING_DIM,
    )

    result = _sync({"entities": _entities(2)}, user_id="michele")

    ids = {r[0] for r in conn.execute("SELECT id FROM rag_items WHERE collection_name = 'ha_entities'")}
    assert "ha:light.kitchen_0" not in ids
    assert {"ha:michele:light.kitchen_0", "ha:michele:light.kitchen_1"} <= ids
    # The entity is still in HA, so the gateway must not clear its state cache.
    assert result["orphaned_entity_ids"] == []
