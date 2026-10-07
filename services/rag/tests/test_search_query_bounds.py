"""The search-query length bound that stops ``sharedllm_rag`` OOM-killing.

Production symptom: the RAG container was being OOM-killed by the kernel every
~6 minutes (300+ restarts in 24 hours). The kill always happened while a
``POST /rag/search`` was running, and the process grew in visible steps from
~1.6 GiB to its 6 GiB cgroup ceiling before dying.

Root cause, measured against the live 4,184-row index (see the ``SearchRequest``
docstring for the full table): ``_bm25_search`` hands the entire query to FTS5
as a single quoted phrase, and a phrase's cost grows superlinearly with its
length. 500 chars costs 0.7 s / +42 MiB; 8,000 chars costs 13 s / +890 MiB;
40,000 chars allocates 4.5 GiB in about two seconds and then pins the process at
its ceiling until the kernel kills it.

Isolation notes (so these are not re-derived): ``k`` is NOT the trigger (flat
from k=8 to k=1,000,000); the collection is NOT the trigger (an empty
collection produced the same +4.5 GiB, so it is independent of the result set);
the allocation happens whether or not anything matches.
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.rag import main as rag_main
from services.rag.main import require_internal
from services.rag.schemas import SearchRequest

LIMIT = SearchRequest.MAX_QUERY_CHARS


def _request(query: str) -> SearchRequest:
    return SearchRequest(query=query, user_id="default", collection_name="calibre_files", k=8)


def test_the_bound_is_where_the_measurements_say_it_is():
    assert LIMIT == 2000
    assert LIMIT <= 4000, (
        "4,000 chars already costs 5.5 s and +231 MiB; anything at or above "
        "that is inside the measured danger zone."
    )


def test_the_docstring_records_the_measurements_that_justify_the_bound():
    doc = SearchRequest.__doc__ or ""
    for expected in ("40,000", "+4,487 MiB", "OOM", "superlinearly"):
        assert expected in doc, f"the docstring no longer records {expected!r}"


def test_a_query_at_the_limit_still_searches():
    assert _request("x" * LIMIT).query == "x" * LIMIT


def test_a_query_one_char_over_the_limit_is_refused():
    with pytest.raises(ValidationError) as exc:
        _request("x" * (LIMIT + 1))
    detail = str(exc.value)
    assert "query" in detail
    assert str(LIMIT) in detail


def test_the_refusal_names_the_field_and_not_some_other_field():
    with pytest.raises(ValidationError) as exc:
        _request("y" * (LIMIT + 1))
    loc = exc.value.errors()[0]["loc"]
    assert "query" in loc


def test_the_description_tells_the_caller_what_to_do_about_it():
    description = SearchRequest.model_fields["query"].description or ""
    assert "2000" in description
    assert "refused" in description


def test_the_http_endpoint_refuses_before_touching_the_index(monkeypatch):
    """A 422 must come back from validation alone — the handler, the embedder
    and FTS5 must never run, because running them is what killed the container."""

    def _never_called(*_args, **_kwargs):
        raise AssertionError("the expensive path must not be reached")

    monkeypatch.setattr(rag_main, "embed", _never_called)
    client = TestClient(rag_main.app)
    client.app.dependency_overrides[require_internal] = lambda: None
    try:
        resp = client.post(
            "/rag/search",
            json={
                "query": "x " * 20000,
                "user_id": "default",
                "collection_name": "calibre_files",
                "k": 8,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()
    errors = body.get("detail") or []
    assert any("query" in str(err.get("loc", "")) for err in errors)


def test_a_normal_search_still_validates_on_the_http_path(monkeypatch):
    """Guards the bound from breaking ordinary traffic: a realistic query must
    still pass validation and reach the handler and the embedder."""
    seen: list[list[str]] = []

    def _recording_embed(texts):
        seen.append(list(texts))
        raise AssertionError("stop before the vector store (unset in this test)")

    monkeypatch.setattr(rag_main, "embed", _recording_embed)
    client = TestClient(rag_main.app)
    client.app.dependency_overrides[require_internal] = lambda: None
    try:
        resp = client.post(
            "/rag/search",
            json={
                "query": "What did Macduff say about trusting God in poverty and trial?",
                "user_id": "default",
                "collection_name": "calibre_files",
                "k": 8,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert resp.status_code != 422, "a realistic query was refused by the bound"
    assert len(seen) == 1, "validation passed but the handler did not embed the query"
    assert seen[0][0].startswith("What did Macduff")

    # Either 500 (validation passed, handler then failed on unset globals) or a
    # successful response — but never the 422 that means the bound is too tight.
    assert resp.status_code != 422, "a realistic query was refused by the bound"
