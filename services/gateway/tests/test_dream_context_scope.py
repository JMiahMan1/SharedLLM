"""``include_curriculum=False`` must drop the DREAM block as well.

The DREAM validation checklist lived outside the curriculum guard, so a research
turn that asked for no curriculum still got that block appended. The block is
nearly always non-empty in production, which made ``context`` non-empty, which
made ``grounded=bool(context.strip())`` true, so the turn was told it was
grounded in material it did not have and was denied the search tools it needed.

On 2026-10-06 that chain produced a confident four-kilobyte answer about
Shakespeare's Macbeth for a question about John R. Macduff: retrieval was down
at the moment, the only surviving context was one DREAM lesson, and the model
had nothing to answer from. These tests pin the guard so the block cannot leak
back in.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from services.gateway import orchestrator

QUERY = "What did Macduff say about trusting God in poverty and trial?"
LIBRARY_HIT = {
    "content": "Faith must rest satisfied with what is baffling to sight.",
    "metadata": {"title": "Memories of Bethany", "author": "John R. Macduff", "chapter": "XIV."},
}
DREAM_PENDING = {"validations_pending": [{"id": "lesson-abc", "rule": "ping", "applied_count": 2}]}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload


class _Session:
    """Stands in for the gateway's aiohttp session."""

    def __init__(self, dream=DREAM_PENDING, hits=(LIBRARY_HIT,)):
        self.dream = dream
        self.hits = hits
        self.posted: list[str] = []
        self.got: list[str] = []

    async def post(self, url, **kwargs):
        self.posted.append(url)
        if url.endswith("/rag/search"):
            return _Resp({"results": list(self.hits)})
        return _Resp({"result": {}})

    async def get(self, url, **kwargs):
        self.got.append(url)
        if "dream/pending" in url:
            return _Resp(self.dream)
        if "rag/learning" in url:
            return _Resp({"items": []})
        return _Resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_all_settings", AsyncMock(return_value={"rag_svc_url": "http://rag:8004"})
    )


async def _context(session, *, include_curriculum: bool) -> str:
    with patch("services.gateway.main.shared_http_client", return_value=session):
        return await orchestrator._fetch_rag_context(
            QUERY,
            "default",
            None,
            workspace_id="ws-1",
            include_curriculum=include_curriculum,
            include_library=True,
        )


async def test_the_dream_block_is_dropped_when_curriculum_is_dropped(settings):
    session = _Session()

    context = await _context(session, include_curriculum=False)

    assert "[DREAM —" not in context
    assert not any("dream/pending" in url for url in session.got)


async def test_the_dream_block_still_ships_with_the_curriculum(settings):
    session = _Session()

    context = await _context(session, include_curriculum=True)

    assert "[DREAM — VALIDATE ON NEXT MISSION" in context
    assert "lesson-abc" in context
    assert any("dream/pending" in url for url in session.got)


async def test_dropping_the_dream_block_does_not_drop_the_passages(settings):
    """The guard must not take the retrieval with it -- only the furniture."""
    session = _Session()

    context = await _context(session, include_curriculum=False)

    assert "baffling to sight" in context
    assert "Memories of Bethany" in context


async def test_a_prose_hit_keeps_its_metadata_for_the_citation(settings):
    """Lesson JSON re-parsing must not wipe the metadata of ordinary hits.

    The compact-lesson branch re-derived ``_meta`` from ``content`` for every
    collection, so prose hit metadata was thrown away and ``_citation_suffix``
    always received an empty dict: the model was handed book passages with no
    title, author or chapter to attribute them to.
    """
    session = _Session()

    context = await _context(session, include_curriculum=False)

    assert "(Source: Memories of Bethany by John R. Macduff, chapter XIV.)" in context


async def test_a_failed_retrieval_leaves_the_context_empty(settings):
    """A failed search must read as no context, so grounding stays off.

    Anything surviving a failed retrieval would make ``grounded`` true while the
    model had no material, which is the hallucination this pin exists to stop.
    """
    session = _Session(dream={"validations_pending": []}, hits=())

    context = await _context(session, include_curriculum=False)

    assert context.strip() == ""


async def test_an_unreachable_rag_leaves_the_context_empty(settings):
    """RAG being down must not leave a half-built context behind either."""
    session = _Session()
    session.post = AsyncMock(side_effect=ConnectionError("rag is down"))

    context = await _context(session, include_curriculum=False)

    assert context.strip() == ""


def test_the_dream_block_sits_inside_the_curriculum_guard():
    """Structural pin: the guard must wrap the block, not merely precede it."""
    source = inspect.getsource(orchestrator._fetch_rag_context)

    curriculum_guard = source.index("if not skip_curriculum:")
    dream_guard = source.index("if not skip_curriculum and (workspace_id")
    block = source.index("[DREAM — VALIDATE ON NEXT MISSION")

    assert curriculum_guard < dream_guard < block
    assert source.count("if not skip_curriculum") == 2
