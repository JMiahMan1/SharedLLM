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
