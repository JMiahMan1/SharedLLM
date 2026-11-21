"""``min_similarity``: a relevance floor for /rag/search.

The vector index always returns its k nearest rows, however unrelated they are,
so every collection answered every question: "What's the verse of the day?" came
back with fifteen devices, fifteen capabilities and fifteen lessons. The result's
``score`` is a rank fusion and cannot tell a close hit from the least-bad one, so
each result now carries its real cosine ``similarity`` and a caller can ask for
nothing below a floor.

Also pinned: a failed search is a 503, never an empty list that reads as "no
matches" while the index is broken.
"""
import asyncio

import pytest
from fastapi import HTTPException

from services.rag import main as rag_main
from services.rag.schemas import SearchRequest
from services.rag.store import NumpyVecAdapter, SqliteVecAdapter
from services.rag.tests.test_rag_search import _ingest, _install


def _search(query, *, min_similarity=None, k=5, collection="home_devices"):
    return asyncio.run(rag_main.search(SearchRequest(
        query=query, user_id="default", k=k, collection_name=collection, min_similarity=min_similarity,
    ))).results


def _seed():
    for text in (
        "porch light front porch lamp",
        "kitchen ceiling light kitchen",
        "garage door opener garage",
    ):
        asyncio.run(_ingest("default", text, "home_devices"))


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_every_hit_reports_its_cosine_similarity(tmp_path, adapter_cls):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    _seed()
    hits = _search("porch light")
    assert hits, "the nearest rows still come back without a floor"
    top = hits[0]
    assert "porch" in top.content
    assert top.similarity is not None and 0.5 < top.similarity <= 1.0
    assert all(h.similarity is not None for h in hits)
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_without_a_floor_an_unrelated_question_still_gets_every_row(tmp_path, adapter_cls):
    """The behaviour the floor exists to fix: k nearest, whatever they are."""
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    _seed()
    assert len(_search("verse of the day")) == 3


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_a_floor_drops_rows_that_are_merely_nearest(tmp_path, adapter_cls):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    _seed()
    assert _search("verse of the day", min_similarity=0.5) == []
    kept = _search("porch light", min_similarity=0.5)
    assert [h.content for h in kept] == ["porch light front porch lamp"]


def test_a_keyword_phrase_hit_is_kept_whatever_its_similarity(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)
    _seed()
    garage = next(r["id"] for r in rag_main._conn().execute(
        "SELECT id FROM rag_items WHERE content LIKE 'garage%'"
    ).fetchall())
    monkeypatch.setattr(rag_main, "_bm25_search", lambda *a, **k: [(garage, -1.0)])
    monkeypatch.setattr(NumpyVecAdapter, "cosine_similarities", lambda self, q, ids: {i: 0.0 for i in ids})
    kept = _search("garage door", min_similarity=0.9)
    assert [h.content for h in kept] == ["garage door opener garage"]


def test_the_floor_is_validated():
    with pytest.raises(ValueError):
        SearchRequest(query="x", user_id="u", min_similarity=1.5)


def test_a_failed_search_is_a_503_not_an_empty_answer(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)

    def broken(*_a, **_k):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(rag_main, "_hybrid_search", broken)
    with pytest.raises(HTTPException) as exc:
        _search("porch light")
    assert exc.value.status_code == 503
    assert "index unreadable" in exc.value.detail
