"""Read-aloud in pieces: the passage is cut up here, not by the speech engine.

The engine's own long-text splitter re-phonemises an ever-growing span as it
packs sentences, which costs minutes on a whole chapter and refuses one outright
over 510 tokens. These tests pin the two facts that fix buys: a piece is small
enough to speak quickly, and the pieces a reader has already heard are not
spoken a second time.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import services.bible.main as bible_main
from services.bible import narration
from services.bible.models import NarrationAudio
from services.bible.refs import parse_reference

pytestmark = pytest.mark.unit

SECRET = "test-secret"
RIFF = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16


class _Response:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Engine:
    """Replays a queue of responses and records what was asked for."""

    def __init__(self, *responses: _Response):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json or {}))
        if not self.responses:
            raise AssertionError("the speech engine was called more times than expected")
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _audio(payload: bytes = RIFF) -> _Response:
    return _Response(
        200,
        json.dumps(
            {
                "status": "SUCCESS",
                "message": "TTS generated successfully",
                "service": "tts",
                "detail": {
                    "audio_base64": base64.b64encode(payload).decode("utf-8"),
                    "mime_type": "audio/wav",
                    "length_bytes": len(payload),
                },
            }
        ),
    )


def _patch_engine(monkeypatch, engine: _Engine | None):
    def _factory(*args, **kwargs):
        return engine

    monkeypatch.setattr(narration.aiohttp, "ClientSession", _factory)
    monkeypatch.setattr(bible_main.aiohttp, "ClientSession", _factory)


def _small_pieces(monkeypatch, *, first: int = 20, target: int = 30):
    """Shrink the piece size so the mini corpus produces several of them."""
    monkeypatch.setattr(narration, "FIRST_CHUNK_CHARACTERS", first)
    monkeypatch.setattr(narration, "TARGET_CHUNK_CHARACTERS", target)


def _script(sentences: int = 12) -> narration.Script:
    body = " ".join(f"This is sentence number {index} of the passage." for index in range(sentences))
    return narration.Script(reference="Test 1", body=body)


def _pieces(session, reference: str = "Genesis 1") -> list[narration.Chunk]:
    spans = parse_reference("Gen 1")
    script = narration._script_for(session, "kjv", spans, reference)
    return narration.split_script(script)


async def _speak(session, *, reference: str = "Genesis 1", index: int = 0, voice=None) -> dict:
    spans = parse_reference("Gen 1")
    return await narration.narrate_chunk(
        session,
        version="kjv",
        spans=spans,
        reference=reference,
        voice=voice,
        index=index,
        execution_url="http://execution:8003",
        internal_secret=SECRET,
    )


# ── cutting the passage up ──────────────────────────────────────────────────


def test_the_first_piece_is_the_short_one():
    chunks = narration.split_script(_script())
    assert len(chunks) > 1
    assert chunks[0].characters <= narration.FIRST_CHUNK_CHARACTERS


def test_the_pieces_join_back_together():
    script = _script()
    chunks = narration.split_script(script)
    rejoined = " ".join(" ".join(chunk.text.split()) for chunk in chunks)
    assert rejoined == " ".join(script.text.split())


def test_a_piece_ends_where_a_sentence_ends():
    chunks = narration.split_script(_script())
    for chunk in chunks[:-1]:
        assert chunk.text.rstrip()[-1] in ".!?", chunk.text


def test_a_sentence_longer_than_the_target_is_not_cut_in_the_middle():
    long_sentence = ("word " * 200).strip()
    chunks = narration.split_script(narration.Script(reference="Test 1", body=long_sentence))
    assert any(long_sentence == chunk.text.strip() for chunk in chunks), [c.text for c in chunks]


def test_splitting_the_same_script_twice_gives_the_same_pieces():
    first = [chunk.text for chunk in narration.split_script(_script())]
    second = [chunk.text for chunk in narration.split_script(_script())]
    assert first == second


def test_a_piece_is_cached_under_its_own_words():
    same = narration.Chunk(index=0, text="In the beginning God created")
    other = narration.Chunk(index=0, text="In the beginning God said")
    assert narration.chunk_key("kjv", "Gen 1", "v", same) == narration.chunk_key(
        "kjv", "Gen 1", "v", same
    )
    assert narration.chunk_key("kjv", "Gen 1", "v", same) != narration.chunk_key(
        "kjv", "Gen 1", "v", other
    )


# ── planning and speaking pieces ────────────────────────────────────────────


def test_a_plan_needs_no_engine(loaded, monkeypatch):
    _small_pieces(monkeypatch)
    _patch_engine(monkeypatch, None)
    plan = narration.plan(
        loaded,
        version="kjv",
        spans=parse_reference("Gen 1"),
        reference="Genesis 1",
        voice=None,
    )
    assert plan["count"] > 1
    assert len(plan["chunks"]) == plan["count"]
    assert plan["cached_count"] == 0
    assert plan["all_cached"] is False
    assert plan["chunks"][0]["characters"] <= narration.FIRST_CHUNK_CHARACTERS


async def test_a_plan_reports_what_has_already_been_spoken(loaded, monkeypatch):
    _small_pieces(monkeypatch)
    _patch_engine(monkeypatch, _Engine(_audio()))
    await _speak(loaded, index=0)
    plan = narration.plan(
        loaded,
        version="kjv",
        spans=parse_reference("Gen 1"),
        reference="Genesis 1",
        voice=None,
    )
    assert plan["chunks"][0]["cached"] is True
    assert plan["chunks"][1]["cached"] is False
    assert plan["cached_count"] == 1
    assert plan["all_cached"] is False


async def test_the_second_play_of_a_piece_costs_nothing(loaded, monkeypatch):
    _small_pieces(monkeypatch)
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    first = await _speak(loaded, index=0)
    second = await _speak(loaded, index=0)
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["audio_base64"] == first["audio_base64"]
    assert len(engine.calls) == 1
    spoken = _pieces(loaded)[0]
    key = narration.chunk_key("kjv", "Genesis 1", "default", spoken)
    assert loaded.get(NarrationAudio, key) is not None


async def test_a_piece_past_the_end_is_refused(loaded, monkeypatch):
    _small_pieces(monkeypatch)
    _patch_engine(monkeypatch, _Engine(_audio()))
    count = len(_pieces(loaded))
    with pytest.raises(narration.NarrationError) as excinfo:
        await _speak(loaded, index=count + 5)
    assert str(count) in str(excinfo.value)


# ── the routes ──────────────────────────────────────────────────────────────


def test_the_plan_route_lists_the_pieces(loaded_client: TestClient, monkeypatch):
    _small_pieces(monkeypatch)
    resp = loaded_client.get("/narration/plan", params={"ref": "Gen 1", "version": "kjv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reference"] == "Genesis 1"
    assert body["count"] > 1
    assert len(body["chunks"]) == body["count"]
    assert body["all_cached"] is False


def test_the_chunk_route_speaks_a_piece(loaded_client: TestClient, monkeypatch):
    _small_pieces(monkeypatch)
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get(
        "/narration/chunk", params={"ref": "Gen 1", "version": "kjv", "index": 0}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["index"] == 0
    assert body["count"] > 1
    assert base64.b64decode(body["audio_base64"]) == RIFF
    assert engine.calls[0][0].endswith("/execute/tts")


def test_a_piece_past_the_end_is_a_400(loaded_client: TestClient, monkeypatch):
    _small_pieces(monkeypatch)
    _patch_engine(monkeypatch, _Engine(_audio()))
    resp = loaded_client.get(
        "/narration/chunk", params={"ref": "Gen 1", "version": "kjv", "index": 99}
    )
    assert resp.status_code == 400
    assert "99" in resp.json()["detail"]


def test_a_piece_needs_the_internal_secret(engine, monkeypatch):
    _small_pieces(monkeypatch)
    monkeypatch.setattr(bible_main.app.state, "engine", engine)
    with TestClient(bible_main.app) as raw:
        resp = raw.get("/narration/chunk", params={"ref": "Gen 1", "version": "kjv", "index": 0})
    assert resp.status_code == 403
