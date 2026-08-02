"""Tests for the RAG dreaming mode (lesson consolidation)."""

import json
import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.schemas import IngestRequest
from services.rag.store import NumpyVecAdapter

DIM = 8


def _install_fakes(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()

    def fake_embed(texts):
        out = []
        for text in texts:
            v = [0.0] * DIM
            for i, ch in enumerate(text):
                v[i % DIM] += ord(ch)
            out.append(v)
        return out

    rag_main.embed = fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


def _lesson_content(rule: str, summary: str = "default summary") -> str:
    return json.dumps(
        {
            "id": "x",
            "topic": "topic",
            "rule": rule,
            "root_cause": "rc",
            "outcome": "success",
            "confidence": 0.5,
            "tags": ["test"],
            "summary": summary,
        },
        ensure_ascii=False,
    )


async def _ingest(rule: str, summary: str, confidence: float = 0.5) -> str:
    req = IngestRequest(
        user_id="default",
        content=_lesson_content(rule, summary),
        collection_name="system_learnings",
        metadata={
            "topic": "Raven lesson: test",
            "rule": rule,
            "root_cause": "rc",
            "outcome": "success",
            "confidence": confidence,
            "tags": ["test"],
            "type": "learning",
            "id": "unused",
        },
    )
    resp = await rag_main.ingest(req)
    return resp["id"]


async def _learnings() -> list[dict]:
    return (await rag_main.list_learnings())["items"]


@pytest.mark.asyncio
async def test_dream_merges_same_rule_lessons(tmp_path):
    _install_fakes(tmp_path)
    low = await _ingest("Always verify with tests", "low-confidence version", 0.6)
    high = await _ingest("Always verify with tests", "high-confidence version", 0.95)
    other = await _ingest("Different rule entirely", "untouched", 0.8)

    report = await rag_main.dream_learnings()

    assert report["status"] == "SUCCESS"
    assert report["reviewed"] == 3
    # The 0.95-confidence lesson survives; the 0.6 one is merged away.
    assert list(report["merged"].keys()) == [high]
    assert report["merged"][high] == [low]

    items = await _learnings()
    ids = {i["id"] for i in items}
    assert ids == {high, other}
    survivor = next(i for i in items if i["id"] == high)
    assert survivor["supersedes"] == [low]
    assert survivor["metadata"]["supersedes"] == [low]


@pytest.mark.asyncio
async def test_dream_compacts_oversized_lessons(tmp_path):
    _install_fakes(tmp_path)
    big = await _ingest("Keep lessons short", "A" * 900, 0.7)

    report = await rag_main.dream_learnings(compact_at=600, summary_len=400)

    assert report["status"] == "SUCCESS"
    assert report["compacted"] == [big]
    items = await _learnings()
    content = items[0]["content"]
    assert len(content) <= 700
    assert len(json.loads(content)["summary"]) == 400


@pytest.mark.asyncio
async def test_dream_prunes_superseded_lessons(tmp_path):
    conn = _install_fakes(tmp_path)
    keep = await _ingest("Use the current approach", "keeper", 0.9)
    old = await _ingest("Old approach superseded", "old version", 0.5)
    # Make lesson-old superseded by lesson-keep.
    conn.execute(
        "UPDATE rag_items SET supersedes = ? WHERE id = ?",
        [json.dumps([old]), keep],
    )
    conn.commit()

    report = await rag_main.dream_learnings()

    assert report["status"] == "SUCCESS"
    assert report["pruned"] == [old]
    items = await _learnings()
    assert [i["id"] for i in items] == [keep]


@pytest.mark.asyncio
async def test_dream_sums_usage_and_applied_counts(tmp_path):
    conn = _install_fakes(tmp_path)
    low = await _ingest("Counts accumulate", "first", 0.6)
    high = await _ingest("Counts accumulate", "second", 0.9)
    conn.execute(
        "UPDATE rag_items SET usage_count = 5, applied_count = 2 WHERE id = ?", [low]
    )
    conn.execute(
        "UPDATE rag_items SET usage_count = 3, applied_count = 1 WHERE id = ?", [high]
    )
    conn.commit()

    await rag_main.dream_learnings()

    survivor = next(i for i in await _learnings() if i["id"] == high)
    assert survivor["usage_count"] == 8
    assert survivor["applied_count"] == 3


@pytest.mark.asyncio
async def test_dream_keeps_unrelated_lessons(tmp_path):
    _install_fakes(tmp_path)
    await _ingest("Rule one", "a", 0.5)
    await _ingest("Rule two", "b", 0.5)

    report = await rag_main.dream_learnings()

    assert report["compacted"] == []
    assert report["merged"] == {}
    assert report["pruned"] == []
    assert report["kept"] == 2
