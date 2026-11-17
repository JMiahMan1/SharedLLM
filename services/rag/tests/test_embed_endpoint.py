"""POST /rag/embed: vectors for other services from the loaded model."""
import asyncio

import pytest
from fastapi import HTTPException

from services.rag import main as rag_main


@pytest.fixture
def fake_model(monkeypatch):
    seen: list[list[str]] = []

    def _embed(texts):
        seen.append(list(texts))
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    monkeypatch.setattr(rag_main, "embedder", object())
    monkeypatch.setattr(rag_main, "embed", _embed)
    monkeypatch.setattr(rag_main, "embed_batched", lambda texts, **_k: _embed(texts))
    monkeypatch.setattr(rag_main, "EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    return seen


def _call(**body):
    return asyncio.run(rag_main.embed_texts(rag_main.EmbedRequest(**body)))


def test_queries_and_documents_get_the_models_task_prefix(fake_model):
    out = _call(texts=["worry"], kind="query")
    assert fake_model[-1] == ["search_query: worry"]
    assert out["model"] == "nomic-ai/nomic-embed-text-v1.5" and out["dim"] == 3
    _call(texts=["Do not be anxious"])
    assert fake_model[-1] == ["search_document: Do not be anxious"]


def test_a_model_without_task_prefixes_gets_the_text_as_is(fake_model, monkeypatch):
    monkeypatch.setattr(rag_main, "EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    _call(texts=["worry"], kind="query")
    assert fake_model[-1] == ["worry"]


@pytest.mark.parametrize(
    "body",
    [{"texts": []}, {"texts": ["x"] * 129}, {"texts": ["x" * 2001]}],
)
def test_oversized_or_empty_requests_are_refused(fake_model, body):
    with pytest.raises(HTTPException) as err:
        _call(**body)
    assert err.value.status_code == 400


def test_no_model_loaded_is_a_503(monkeypatch):
    monkeypatch.setattr(rag_main, "embedder", None)
    with pytest.raises(HTTPException) as err:
        _call(texts=["worry"])
    assert err.value.status_code == 503
