"""POST /rag/rerank: how well each text answers a question, from the cross-encoder."""
import asyncio

import pytest
from fastapi import HTTPException

from services.rag import main as rag_main


class FakeCrossEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query, texts, batch_size=16):
        self.calls.append((query, list(texts)))
        return [float(len(set(query.split()) & set(t.split()))) for t in texts]


@pytest.fixture
def encoder(monkeypatch):
    fake = FakeCrossEncoder()
    monkeypatch.setattr(rag_main, "reranker", fake)
    return fake


def _call(**body):
    return asyncio.run(rag_main.rerank_texts(rag_main.RerankRequest(**body)))


def test_scores_come_back_in_the_texts_order(encoder):
    out = _call(query="do not worry", texts=["sing a song", "do not worry about tomorrow"])
    assert out == {"model": rag_main.RERANK_MODEL, "scores": [0.0, 3.0]}
    assert encoder.calls == [("do not worry", ["sing a song", "do not worry about tomorrow"])]


@pytest.mark.parametrize(
    "body",
    [
        {"query": " ", "texts": ["x"]},
        {"query": "q", "texts": []},
        {"query": "q", "texts": ["x"] * (rag_main.RERANK_MAX_TEXTS + 1)},
        {"query": "q", "texts": ["x" * (rag_main.RERANK_MAX_CHARS + 1)]},
        {"query": "q" * (rag_main.RERANK_MAX_CHARS + 1), "texts": ["x"]},
    ],
)
def test_bad_requests_are_refused_before_the_model_runs(encoder, body):
    with pytest.raises(HTTPException) as err:
        _call(**body)
    assert err.value.status_code == 400
    assert encoder.calls == []


def test_a_model_that_fails_is_a_clear_503(monkeypatch):
    class Broken:
        def rerank(self, *_a, **_k):
            raise RuntimeError("onnx session died")

    monkeypatch.setattr(rag_main, "reranker", Broken())
    with pytest.raises(HTTPException) as err:
        _call(query="q", texts=["x"])
    assert err.value.status_code == 503


def test_the_model_is_loaded_once(monkeypatch):
    made: list[str] = []

    class Loaded(FakeCrossEncoder):
        def __init__(self, model_name):
            super().__init__()
            made.append(model_name)

    import sys
    import types

    # fastembed is only in the service image: stand in for its module.
    for name in ("fastembed", "fastembed.rerank"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or types.ModuleType(name))
    module = types.ModuleType("fastembed.rerank.cross_encoder")
    module.TextCrossEncoder = Loaded
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", module)
    monkeypatch.setattr(rag_main, "reranker", None)
    first = rag_main._get_reranker()
    assert rag_main._get_reranker() is first
    assert made == [rag_main.RERANK_MODEL]
