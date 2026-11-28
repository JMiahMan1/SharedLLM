"""Fixes that make hybrid search usable for natural language.

Pins four production bugs: FTS5 was handed the whole question as one quoted
phrase (so natural language matched nothing), nomic's task prefixes were applied
on ``/rag/embed`` but not on ingest/search, the loaded cross-encoder was never
used by ``/rag/search``, and the dream validation checklist mixed validated and
unvalidated lessons.
"""
from __future__ import annotations

import asyncio
import json

from services.rag import main as rag_main
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import NumpyVecAdapter
from services.rag.tests.test_rag_search import _fake_embed, _ingest, _install


def test_bm25_query_tokens_are_quoted_and_operators_are_data():
    query = rag_main._bm25_query('How do I fix the "garage-door" AND OR NOT NEAR?')
    assert '"garage"' in query and '"door"' in query
    # Reserved words are quoted, never parsed as FTS5 syntax.
    assert '"and"' in query and '"or"' in query and '"not"' in query and '"near"' in query
    assert query.startswith("(") or '"how"' in query
    assert '"garage-door"' not in query


def test_bm25_query_empty_for_only_punctuation():
    assert rag_main._bm25_query("?!...---") == ""
    assert rag_main._bm25_search("any", "u", "?!...---", 5) == []


def test_bm25_natural_language_matches_tokens_not_a_contiguous_phrase(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    asyncio.run(_ingest("default", "open the garage door with the wall button", "home_devices"))
    asyncio.run(_ingest("default", "kitchen ceiling light", "home_devices"))

    hits = rag_main._bm25_search(
        "home_devices", "default", "how do I open the garage door?", 10
    )
    contents = [
        rag_main._conn().execute("SELECT content FROM rag_items WHERE id = ?", [doc_id]).fetchone()["content"]
        for doc_id, _ in hits
    ]
    assert any("garage" in text for text in contents)
    assert all("ceiling" not in text for text in contents)


def test_bm25_reserved_words_and_hyphens_never_raise(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    asyncio.run(_ingest("default", "AND OR NOT NEAR and the multi-word token", "system_learnings"))
    # Would be sqlite3.OperationalError if the words reached FTS5 as operators.
    hits = rag_main._bm25_search("system_learnings", "default", "AND OR NOT NEAR multi-word", 10)
    assert hits


def test_short_query_also_gets_the_exact_phrase_alternative():
    query = rag_main._bm25_query("porch light")
    assert '"porch" OR "light"' in query
    assert query.endswith('"porch light"')


def test_nomic_document_prefix_on_ingest_and_query_prefix_on_search(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)
    monkeypatch.setattr(rag_main, "EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    seen: list[list[str]] = []

    def recording_embed(texts):
        seen.append(list(texts))
        return _fake_embed(texts)

    monkeypatch.setattr(rag_main, "embed", recording_embed)

    asyncio.run(_ingest("default", "my note about lamps", "nextcloud_files"))
    assert seen[0] == ["search_document: my note about lamps"]

    seen.clear()
    asyncio.run(rag_main.search(SearchRequest(
        query="my note about lamps", user_id="default", collection_name="nextcloud_files",
    )))
    assert seen[0] == ["search_query: my note about lamps"]


def test_task_prefix_is_applied_exactly_once():
    assert rag_main._with_task_prefix("search_document: already", "document") == "search_document: already"
    assert rag_main._with_task_prefix("search_query: already", "query") == "search_query: already"
    assert rag_main._with_task_prefix("plain", "query") == "search_query: plain"


def test_a_model_without_task_prefixes_is_left_alone(monkeypatch):
    monkeypatch.setattr(rag_main, "EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    assert rag_main._with_task_prefix("plain", "document") == "plain"


class _Reranker:
    def __init__(self, preferred: str):
        self.preferred = preferred
        self.calls = 0

    def rerank(self, query, texts, batch_size=16):
        self.calls += 1
        return [1.0 if self.preferred in text else 0.0 for text in texts]


def test_search_reranks_the_over_fetched_pool_when_enabled(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)
    asyncio.run(_ingest("default", "alpha one", "nextcloud_files"))
    asyncio.run(_ingest("default", "beta two", "nextcloud_files"))
    asyncio.run(_ingest("default", "target three", "nextcloud_files"))
    monkeypatch.setattr(rag_main, "reranker", _Reranker("target"))

    reranked = asyncio.run(rag_main.search(SearchRequest(
        query="alpha", user_id="default", collection_name="nextcloud_files", k=1,
    ))).results
    plain = asyncio.run(rag_main.search(SearchRequest(
        query="alpha", user_id="default", collection_name="nextcloud_files", k=1, rerank=False,
    ))).results

    assert reranked[0].content == "target three"
    assert plain[0].content == "alpha one"
    assert rag_main.reranker.calls >= 1


def test_a_broken_reranker_falls_back_to_hybrid_order(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)
    asyncio.run(_ingest("default", "alpha one", "nextcloud_files"))
    asyncio.run(_ingest("default", "target three", "nextcloud_files"))

    class Broken:
        def rerank(self, *args, **kwargs):
            raise RuntimeError("model exploded")

    monkeypatch.setattr(rag_main, "reranker", Broken())
    results = asyncio.run(rag_main.search(SearchRequest(
        query="alpha", user_id="default", collection_name="nextcloud_files", k=2,
    ))).results
    assert results and results[0].content == "alpha one"


def _lesson(rule: str) -> str:
    return json.dumps({
        "rule": rule,
        "root_cause": "cause",
        "outcome": "success",
        "confidence": 0.5,
        "summary": rule,
    })


async def _seed_lesson(rule: str, lesson_id: str, applied: int = 0, validations=None) -> str:
    await rag_main.ingest(IngestRequest(
        user_id="default",
        content=_lesson(rule),
        collection_name="system_learnings",
        metadata={
            "rule": rule, "root_cause": "cause", "outcome": "success",
            "confidence": 0.5, "id": lesson_id, "type": "learning",
            "validations": list(validations or []),
        },
    ))
    if applied:
        rag_main._bump_applied([lesson_id])
    if validations is not None:
        meta = json.loads(
            rag_main._conn().execute(
                "SELECT metadata FROM rag_items WHERE id = ?", [lesson_id]
            ).fetchone()["metadata"]
        )
        meta["validations"] = list(validations)
        rag_main._conn().execute(
            "UPDATE rag_items SET metadata = ? WHERE id = ?", [json.dumps(meta), lesson_id]
        )
        rag_main._conn().commit()
    return lesson_id


def _dream_report(result) -> dict:
    if hasattr(result, "body"):
        return json.loads(result.body)
    return result


def test_dream_checklist_holds_only_unvalidated_applied_lessons(tmp_path):
    _install(tmp_path, NumpyVecAdapter)

    async def scenario():
        validated = await _seed_lesson("validated rule", "l-validated", 1, [{"passed": True}])
        failed = await _seed_lesson("failed rule", "l-failed", 1, [{"passed": False}])
        unvalidated = await _seed_lesson("unvalidated rule", "l-unvalidated", 1)
        unused = await _seed_lesson("unused rule", "l-unused", 0)
        report = _dream_report(await rag_main.dream_learnings())
        ids = {item["id"] for item in report["validation_checklist"]}
        return validated, failed, unvalidated, unused, ids

    validated, failed, unvalidated, unused, ids = asyncio.run(scenario())
    assert failed in ids
    assert unvalidated in ids
    assert validated not in ids
    assert unused not in ids


def test_validate_keeps_only_the_latest_five_records(tmp_path):
    _install(tmp_path, NumpyVecAdapter)

    async def scenario():
        lesson_id = await _seed_lesson("validation rule", "l-many", 1)
        for index in range(7):
            await rag_main.validate_lessons(
                {"lesson_id": lesson_id, "passed": index % 2 == 0, "evidence": str(index)}
            )
        return lesson_id

    lesson_id = asyncio.run(scenario())
    meta = json.loads(
        rag_main._conn().execute(
            "SELECT metadata FROM rag_items WHERE id = ?", [lesson_id]
        ).fetchone()["metadata"]
    )
    assert len(meta["validations"]) == 5


def test_pending_keeps_failed_and_unvalidated_lessons(tmp_path):
    _install(tmp_path, NumpyVecAdapter)

    async def scenario():
        passed = await _seed_lesson("passed rule", "l-passed", 1, [{"passed": True}])
        failed = await _seed_lesson("failed rule", "l-failed", 1, [{"passed": False}])
        unvalidated = await _seed_lesson("never validated", "l-unvalidated", 1)
        pending = await rag_main.pending_validations()
        return passed, failed, unvalidated, {item["id"] for item in pending["validations_pending"]}

    passed, failed, unvalidated, ids = asyncio.run(scenario())
    assert failed in ids and unvalidated in ids
    assert passed not in ids
