"""
Integration tests for the HONEST Raven lessons feature:

1. A structured lesson ingested via ``ingest`` is returned by
   ``list_learnings`` with its structured fields (rule / root_cause /
   outcome / confidence / supersedes) and a starting ``applied_count`` of 0.
2. The ``/rag/learning/{id}/applied`` handler (``mark_learning_applied``)
   increments ``applied_count`` WITHOUT touching ``usage_count`` — proving the
   "actually applied" metric is distinct from mere retrieval.
3. The gateway-side apply-citation scanner (mirrors agent_loop.py:4096)
   extracts lesson ids the model cites via ``Apply: [id]``.
"""
import os
import re

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

import numpy as np
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
        for t in texts:
            v = np.zeros(DIM, dtype=np.float32)
            for i, ch in enumerate(t):
                v[i % DIM] += ord(ch)
            out.append(v.tolist())
        return out

    rag_main.embed = fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


@pytest.mark.asyncio
async def test_structured_lesson_roundtrip_and_applied_bump(tmp_path):
    _install_fakes(tmp_path)

    resp = await rag_main.ingest(
        IngestRequest(
            user_id="default",
            content="Always rename master->main before first push.",
            collection_name="system_learnings",
            metadata={
                "topic": "git push branch naming",
                "rule": "Rename default branch to main before pushing.",
                "root_cause": "GitHub defaults new repos to master.",
                "outcome": "success",
                "confidence": 0.92,
                "supersedes": ["lesson-old1", "lesson-old2"],
                "tags": ["git", "branch"],
            },
        )
    )
    assert resp["status"] == "SUCCESS"
    doc_id = resp["id"]

    items = (await rag_main.list_learnings("default")).get("items", [])
    assert len(items) == 1
    item = items[0]
    assert item["rule"] == "Rename default branch to main before pushing."
    assert item["root_cause"] == "GitHub defaults new repos to master."
    assert item["outcome"] == "success"
    assert abs(item["confidence"] - 0.92) < 1e-6
    assert set(item["supersedes"]) == {"lesson-old1", "lesson-old2"}

    # Bump the retrieval counter (what the orchestrator does on inject).
    rag_main._bump_retrieved([doc_id])
    before = (await rag_main.list_learnings("default"))["items"][0]
    assert before["usage_count"] == 1
    assert before["applied_count"] == 0  # not applied yet — honest

    # Model cited this lesson -> apply enforcement PATCH.
    applied = await rag_main.mark_learning_applied(doc_id)
    assert applied["status"] == "SUCCESS"

    after = (await rag_main.list_learnings("default"))["items"][0]
    assert after["applied_count"] == 1
    assert after["usage_count"] == 1  # retrieval unchanged by apply bump
    assert after["applied_count"] <= after["usage_count"] or after["usage_count"] >= 0


@pytest.mark.asyncio
async def test_mark_applied_unknown_id_returns_404(tmp_path):
    _install_fakes(tmp_path)
    resp = await rag_main.mark_learning_applied("does-not-exist-id")
    assert resp.status_code == 404


def test_apply_citation_scanner_extracts_ids():
    """Mirrors the regex in services/gateway/agent_loop.py (apply enforcement)."""

    def scan(ans, action_log):
        cited = re.findall(
            r"apply\s*[:=]?\s*\[([^\]]+)\]",
            (ans or "") + "\n" + "\n".join(action_log[-15:]),
            re.IGNORECASE,
        )
        ids = set()
        for chunk in cited:
            for _id in re.findall(r"[A-Za-z0-9_\-]{6,}", chunk):
                ids.add(_id)
        return sorted(ids)

    ans = (
        "My plan:\n"
        "Apply: [934f8dba-2f9d-4d46-b02e-6ce3256ecada] reuse path rule.\n"
        "Apply: [lesson-abc123] for branch naming."
    )
    assert scan(ans, ["step1 ok"]) == [
        "934f8dba-2f9d-4d46-b02e-6ce3256ecada",
        "lesson-abc123",
    ]
    # No citation -> no bump.
    assert scan("I did not reuse any lesson.", []) == []
