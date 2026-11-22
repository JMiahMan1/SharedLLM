"""Meaning search: the verse embeddings, the background job and the blend.

The embedding model is replaced by a tiny fake whose "meanings" are concept
groups, so "worry", "anxious" and "careful" land together while sharing no
letters: exactly the case word search cannot answer.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import corpus, meaning
from services.bible import main as bible_main
from services.bible.books import BOOKS

CONCEPTS = [
    {"worry", "worried", "anxious", "anxiety", "careful", "troubled", "fear", "afraid"},
    {"shepherd", "sheep", "flock", "pasture"},
    {"love", "loved", "loveth", "charity"},
    {"bread", "eat", "food", "hungry"},
]
DIM = len(CONCEPTS) + 8


def _vector(text: str) -> list[float]:
    v = np.zeros(DIM, dtype=np.float32)
    for word in text.lower().replace(":", " ").split():
        word = word.strip(".,;!?\"'")
        for i, group in enumerate(CONCEPTS):
            if word in group:
                v[i] += 1.0
        v[len(CONCEPTS) + hash(word) % 8] += 0.05
    return (v / (np.linalg.norm(v) or 1.0)).tolist()


class FakeModel:
    def __init__(self, name: str = "fake-embed") -> None:
        self.name = name
        self.calls: list[tuple[str, int]] = []

    def __call__(self, rag_url, secret, texts, kind, timeout):
        self.calls.append((kind, len(texts)))
        return {"model": self.name, "dim": DIM, "vectors": [_vector(t) for t in texts]}


ESV = {
    ("Matt", 6, 1): "Therefore do not be anxious about your life.",
    ("Phil", 4, 1): "Do not be anxious about anything.",
    ("Ps", 23, 1): "The LORD is my shepherd; I shall not want.",
    ("John", 3, 1): "For God so loved the world.",
    ("John", 6, 1): "I am the bread of life.",
    ("Gen", 1, 1): "In the beginning God created the heavens and the earth.",
}
KJV = {**ESV, ("Phil", 4, 1): "Be careful for nothing.", ("Matt", 6, 1): "Take no thought for your life."}


def _source(texts: dict) -> list[dict]:
    return [
        {"abbrev": b["osis"], "name": b["name"],
         "chapters": [[texts.get((b["osis"], c, 1), "Placeholder.")] for c in range(1, b["chapters"] + 1)]}
        for b in BOOKS
    ]


def _install(session: Session, tmp_path: Path, code: str, texts: dict) -> None:
    path = tmp_path / f"{code}.json"
    path.write_text(json.dumps(_source(texts)), encoding="utf-8")
    corpus.import_corpus(session, code=code, name=code.upper(), source_path=path)


class FakeReranker:
    """Scores a text by how much of the question's meaning it shares (the fake concepts)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.prefer: str | None = None

    def __call__(self, rag_url, secret, query, texts, timeout):
        self.calls.append((query, list(texts)))
        q = np.asarray(_vector(query))
        scores = [float(np.asarray(_vector(t)) @ q) * 10 for t in texts]
        if self.prefer:
            scores = [s + (20 if self.prefer in t else 0) for s, t in zip(scores, texts, strict=True)]
        return {"model": "fake-rerank", "scores": scores}


@pytest.fixture
def reranker(monkeypatch) -> FakeReranker:
    fake = FakeReranker()
    monkeypatch.setattr(meaning, "_post_rerank", fake)
    return fake


@pytest.fixture
def model(monkeypatch, reranker) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(meaning, "_post_embed", fake)
    monkeypatch.setattr(bible_main, "RAG_SVC_URL", "http://rag.test")
    meaning._cache.invalidate()
    return fake


@pytest.fixture
def bible(loaded_client: TestClient, reader_db: Session, tmp_path: Path, engine, model) -> TestClient:
    _install(reader_db, tmp_path, "esv", ESV)
    _install(reader_db, tmp_path, "kjv", KJV)
    meaning.embed_pass(engine, version="esv", rag_url="http://rag.test", secret="s", pause=0)
    return loaded_client


def _search(client: TestClient, q: str, **params) -> dict:
    resp = client.get("/search", params={"q": q, "match": "meaning", "version": "kjv", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refs(body: dict) -> list[str]:
    return [r["reference"] for r in body["results"]]


def test_a_meaning_search_finds_verses_that_never_use_the_word(bible):
    body = _search(bible, "verses about worry")
    assert set(_refs(body)[:2]) == {"Matthew 6:1", "Philippians 4:1"}
    # Shown in the reader's translation, which says neither "worry" nor "anxious".
    phil = next(r for r in body["results"] if r["reference"] == "Philippians 4:1")
    assert phil["text"] == "Be careful for nothing." and phil["version"] == "kjv"
    assert body["mode"] == "meaning" and body["meaning_version"] == "esv"
    assert body["meaning_coverage"] == 1.0
    assert "Genesis 1:1" not in _refs(body)


def test_scopes_still_apply(bible):
    assert _refs(_search(bible, "worry in:Phil")) == ["Philippians 4:1"]


def test_an_exact_word_match_stays_near_the_top(bible):
    assert _refs(_search(bible, "shepherd"))[0] == "Psalms 23:1"


def test_the_question_is_embedded_as_a_query(bible, model):
    model.calls.clear()
    _search(bible, "afraid")
    assert model.calls == [("query", 1)]


def test_the_job_resumes_and_redoes_vectors_from_another_model(engine, bible, model, monkeypatch):
    assert meaning.embed_pass(engine, version="esv", rag_url="x", secret="s", pause=0) == 0
    monkeypatch.setattr(meaning, "_post_embed", FakeModel("new-model"))
    redone = meaning.embed_pass(engine, version="esv", rag_url="x", secret="s", pause=0)
    with Session(engine) as session:
        verses = session.connection().exec_driver_sql(
            "SELECT count(*) FROM bibleverse WHERE version_code = 'esv'").scalar()
        models = session.connection().exec_driver_sql("SELECT DISTINCT model FROM verse_vector").all()
    assert redone == verses and models == [("new-model",)]


def test_not_ready_is_a_clear_503(loaded_client, model):
    resp = loaded_client.get("/search", params={"q": "worry", "match": "meaning"})
    assert resp.status_code == 503 and "still being prepared" in resp.json()["detail"]


def test_an_unreachable_model_is_a_clear_503(bible, monkeypatch):
    import urllib.error

    def down(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(meaning, "_post_embed", down)
    resp = bible.get("/search", params={"q": "worry", "match": "meaning"})
    assert resp.status_code == 503 and "could not reach" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("installed", "configured", "chosen"),
    [(["kjv", "esv", "web"], None, "esv"), (["kjv", "web"], None, "web"), (["kjv", "esv"], "kjv", "kjv"),
     (["kjv"], "esv", "kjv"), ([], None, None)],
)
def test_the_embedded_translation(installed, configured, chosen):
    assert meaning.choose_version(installed, configured) == chosen


def test_content_words_drop_the_filler():
    assert meaning.content_words("What does the Bible say about worry?") == ["worry"]


def test_fusion_keeps_strong_word_matches_and_drops_stray_ones():
    a, b, c, d = ("Gen", 1, 1), ("Gen", 1, 2), ("Gen", 1, 3), ("Gen", 1, 4)
    fused = meaning.fuse([a, b], [b, c] + [("Rev", 1, i) for i in range(1, 30)] + [d])
    assert fused[0] == b and c in fused and d not in fused


@pytest.fixture
def answers(monkeypatch):
    from services.bible import study_ask

    prompts: list[str] = []

    async def fake_ask(*, prompt, **_kw):
        prompts.append(prompt)
        return "See Philippians 4:1 and Matthew 6:1; compare 1 Peter 5:1, 1 Peter 5:7 and Hezekiah 3:1."

    monkeypatch.setattr(study_ask, "ask", fake_ask)
    return prompts


def test_ask_answers_from_the_verses_meaning_search_found(bible, answers):
    resp = bible.post("/search/ask", params={"username": "sam"}, json={"question": "What should I do when I worry?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retrieval"] == "meaning"
    assert "Philippians 4:1 (KJV): Be careful for nothing." in answers[0]
    refs = {r["reference"]: r["in_results"] for r in body["references"]}
    # Real verses are kept (and say whether the model was given them); an invented book is dropped.
    assert refs["Philippians 4:1"] is True and refs["1 Peter 5:1"] is False
    assert "1 Peter 5:7" not in refs  # not in this tiny test Bible
    assert not any("Hezekiah" in r for r in refs)


def test_ask_falls_back_to_word_search_when_meaning_is_not_ready(loaded_client, reader_db, tmp_path, model, answers):
    _install(reader_db, tmp_path, "esv", ESV)
    resp = loaded_client.post("/search/ask", params={"username": "sam"},
                              json={"question": "Where is the shepherd?", "version": "esv"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["retrieval"] == "words"
    assert "Psalms 23:1" in answers[0]


def test_the_reranker_reads_the_question_with_the_meaning_translation(bible, reranker):
    body = _search(bible, "verses about worry")
    assert body["reranked"] is True
    query, texts = reranker.calls[-1]
    assert query == "verses about worry"
    # The modern translation the index was built from, not the reader's KJV.
    assert "Do not be anxious about anything." in texts
    assert "Be careful for nothing." not in texts


def test_the_rerankers_order_wins(bible, reranker):
    reranker.prefer = "anything"
    assert _refs(_search(bible, "verses about worry"))[0] == "Philippians 4:1"
    reranker.prefer = "your life"
    assert _refs(_search(bible, "verses about worry"))[0] == "Matthew 6:1"


def test_without_the_reranker_the_blend_still_answers(bible, monkeypatch):
    def down(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(meaning, "_post_rerank", down)
    body = _search(bible, "verses about worry")
    assert body["reranked"] is False
    assert set(_refs(body)[:2]) == {"Matthew 6:1", "Philippians 4:1"}


def test_a_wrong_number_of_scores_is_not_trusted(bible, monkeypatch):
    monkeypatch.setattr(meaning, "_post_rerank", lambda *a, **k: {"scores": [1.0]})
    assert _search(bible, "verses about worry")["reranked"] is False


def test_off_topic_verses_drop_out_but_a_few_always_stay():
    positions = [("Gen", 1, i) for i in range(1, 11)]
    # Three answers near the best, then a cliff.
    scores = [3.0, 2.5, -1.0, -9.0, -9.5, -10.0, -10.5, -11.0, -11.5, -12.0]
    kept = meaning.rerank_order(positions, scores)
    assert kept[:3] == positions[:3]
    assert len(kept) == meaning.RERANK_MIN_RESULTS  # the cliff is cut, five always stay
    assert all(p in positions for p in kept)
    # Everything near the best is kept, in score order.
    close = meaning.rerank_order(positions[:4], [1.0, 2.0, 0.5, -3.0])
    assert close == [positions[1], positions[0], positions[2], positions[3]]
    assert meaning.rerank_order([], []) == []


def test_pronouns_are_not_searched_as_words():
    assert meaning.content_words("forgiving someone who hurt you") == ["forgiving", "hurt"]


def test_a_whole_bible_question_goes_online_with_only_the_found_verses(bible, monkeypatch):
    """Local AI offline: the search's verses (reference and text), the translation, the question
    and the reading level go to the online fallback, and the answer carries the note."""
    from services.bible import study_ask

    async def offline(**_kw):
        raise study_ask.StudyAskOffline("The local AI is offline right now.")

    sent: list[dict] = []

    async def online(payload, **_kw):
        sent.append(payload)
        return "See Philippians 4:1.", "Answered by an online model while Jarvis's local AI is offline."

    monkeypatch.setattr(study_ask, "ask", offline)
    monkeypatch.setattr(study_ask, "ask_online", online)
    resp = bible.post("/search/ask", params={"username": "reader-sam-secret"},
                      json={"question": "What should I do when I worry?", "depth": "child"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["online_note"].startswith("Answered by an online model")
    [payload] = sent
    assert set(payload) == {"feature", "reference", "verses", "translation", "question", "reading_level"}
    assert payload["reference"] == "" and payload["reading_level"] == "child"
    assert all(set(v) == {"reference", "text"} for v in payload["verses"])
    assert "reader-sam-secret" not in json.dumps(payload)
