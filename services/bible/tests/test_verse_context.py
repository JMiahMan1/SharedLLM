"""Context for the Verse of the Day: written once, grounded, and never guessed.

The card shows a single verse, so the note is built from the chapter around it
and the study notes already installed for that translation. These tests pin
three things: the prompt carries that evidence, a stored note is reused per
translation and verse, and a failed generation leaves the old note readable
instead of replacing it with an error or an empty string.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.bible import study_ask, verse_context, votd
from services.bible.models import VerseContext


pytestmark = pytest.mark.unit


VERSE = {
    "version": "kjv",
    "osis": "Gen",
    "book": "Gen",
    "book_name": "Genesis",
    "chapter": 1,
    "verse": 1,
    "reference": "Genesis 1:1",
    "text": "In the beginning God created the heaven and the earth.",
}


def _made(body="God is the subject of the opening sentence."):
    return {
        "body": body,
        "model": "assistant",
        "edition_code": "",
        "verses_used": 3,
        "notes_used": 0,
    }


# the note itself


def test_the_key_is_one_note_per_translation_and_verse():
    assert verse_context.key("kjv", "Gen", 1, 1) == "kjv:Gen:1:1"
    assert verse_context.key("nkjv", "Gen", 1, 1) != verse_context.key("kjv", "Gen", 1, 1)


def test_the_prompt_carries_the_verse_its_surroundings_and_notes():
    prompt = verse_context.build_prompt(
        verse=VERSE,
        window=[
            {"reference": "Genesis 1:1", "text": VERSE["text"]},
            {"reference": "Genesis 1:2", "text": "And the earth was without form."},
        ],
        notes=[
            {
                "reference": "Genesis 1:1",
                "kind": "commentary",
                "body": "The Hebrew opens with a broad statement before the days begin.",
            }
        ],
    )
    assert prompt.startswith("Verse of the Day: Genesis 1:1")
    assert "The verse:\nGenesis 1:1 In the beginning" in prompt
    assert "The surrounding passage:" in prompt
    assert "Genesis 1:2 And the earth was without form." in prompt
    assert "Study notes for this passage:" in prompt
    assert "The Hebrew opens with a broad statement" in prompt
    assert "Do not repeat the whole passage or give an application." in prompt


def test_a_verse_with_no_text_is_refused():
    with pytest.raises(study_ask.StudyAskError, match="no text"):
        verse_context.build_prompt(
            verse={**VERSE, "text": "   "}, window=[], notes=[]
        )


def test_cached_payload_is_none_until_a_note_exists(session):
    assert verse_context.cached_payload(session, VERSE) is None


def test_a_stored_note_is_read_back_for_its_own_translation(session):
    session.add(
        VerseContext(
            key=verse_context.key("kjv", "Gen", 1, 1),
            version_code="kjv",
            osis="Gen",
            chapter=1,
            verse=1,
            reference="Genesis 1:1",
            text=VERSE["text"],
            body="The opening of the creation account.",
            model="assistant",
        )
    )
    session.commit()
    payload = verse_context.cached_payload(session, VERSE)
    assert payload["body"] == "The opening of the creation account."
    assert verse_context.cached_payload(session, {**VERSE, "version": "nkjv"}) is None


# generation and storage


async def test_generation_uses_the_context_persona_and_the_installed_edition(
    loaded, monkeypatch
):
    captured = {}

    async def fake_ask(**kwargs):
        captured.update(kwargs)
        return "The chapter opens with a summary before the days are described."

    monkeypatch.setattr(verse_context.study_ask, "ask", fake_ask)
    verse = votd.pick(loaded)
    made = await verse_context.generate(
        loaded,
        verse,
        gateway_url="http://gateway:11435",
        internal_secret="s3cret",
        username="jeremiah",
    )
    assert made["body"].startswith("The chapter opens")
    assert made["verses_used"] >= 1
    assert captured["system"] == verse_context.CONTEXT_SYSTEM_PROMPT
    assert captured["user"] == "jeremiah"
    assert captured["gateway_url"] == "http://gateway:11435"
    assert "The surrounding passage:" in captured["prompt"]


async def test_ensure_writes_once_and_then_reuses_the_note(session, monkeypatch):
    calls = []

    async def fake_generate(session, verse, **kwargs):
        calls.append(verse["reference"])
        return _made()

    monkeypatch.setattr(verse_context, "generate", fake_generate)
    first = await verse_context.ensure(session, VERSE, gateway_url="", internal_secret="")
    second = await verse_context.ensure(session, VERSE, gateway_url="", internal_secret="")
    assert first.key == second.key
    assert calls == ["Genesis 1:1"]
    assert verse_context.cached_payload(session, VERSE)["body"].startswith("God is")


async def test_refresh_replaces_the_body(session, monkeypatch):
    bodies = iter(["The first note.", "A fresh note."])

    async def fake_generate(session, verse, **kwargs):
        return _made(next(bodies))

    monkeypatch.setattr(verse_context, "generate", fake_generate)
    await verse_context.ensure(session, VERSE, gateway_url="", internal_secret="")
    row = await verse_context.ensure(
        session, VERSE, gateway_url="", internal_secret="", refresh=True
    )
    assert row.body == "A fresh note."


async def test_a_failed_refresh_keeps_the_note_the_reader_could_read(session, monkeypatch):
    async def good(session, verse, **kwargs):
        return _made("The note that works.")

    monkeypatch.setattr(verse_context, "generate", good)
    await verse_context.ensure(session, VERSE, gateway_url="", internal_secret="")

    async def bad(session, verse, **kwargs):
        raise study_ask.StudyAskUnavailable("The model is asleep.")

    monkeypatch.setattr(verse_context, "generate", bad)
    with pytest.raises(study_ask.StudyAskUnavailable):
        await verse_context.ensure(
            session, VERSE, gateway_url="", internal_secret="", refresh=True
        )
    assert verse_context.cached_payload(session, VERSE)["body"] == "The note that works."


# the route


def _patch_answer(monkeypatch, *bodies):
    pending = list(bodies)
    calls = []

    async def fake_ask(**kwargs):
        calls.append(kwargs)
        return pending.pop(0) if pending else "A context note."

    monkeypatch.setattr(study_ask, "ask", fake_ask)
    return calls


def test_get_context_writes_a_note_and_returns_the_verse(loaded_client: TestClient, monkeypatch):
    calls = _patch_answer(monkeypatch, "The opening summary of creation.")
    resp = loaded_client.post("/verse-of-day/context", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["context"]["body"] == "The opening summary of creation."
    assert body["verse"]["reference"] == body["context"]["reference"]
    assert body["verse"]["context"]["body"] == "The opening summary of creation."
    assert len(calls) == 1
    assert calls[0]["system"] == verse_context.CONTEXT_SYSTEM_PROMPT


def test_a_stored_note_is_served_without_calling_the_model_again(
    loaded_client: TestClient, monkeypatch
):
    calls = _patch_answer(monkeypatch, "The first answer.")
    first = loaded_client.post("/verse-of-day/context", json={})
    second = loaded_client.post("/verse-of-day/context", json={})
    assert first.json()["context"]["body"] == "The first answer."
    assert second.json()["context"]["body"] == "The first answer."
    assert len(calls) == 1


def test_refresh_asks_the_model_for_new_words(loaded_client: TestClient, monkeypatch):
    calls = _patch_answer(monkeypatch, "The first answer.", "The second answer.")
    loaded_client.post("/verse-of-day/context", json={})
    refreshed = loaded_client.post("/verse-of-day/context", json={"refresh": True})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["context"]["body"] == "The second answer."
    assert len(calls) == 2


def test_the_context_travels_with_verse_of_the_day_and_daily(
    loaded_client: TestClient, monkeypatch
):
    _patch_answer(monkeypatch, "The note the card can show.")
    loaded_client.post("/verse-of-day/context", json={})

    votd_body = loaded_client.get("/verse-of-day").json()
    daily_body = loaded_client.get("/daily").json()
    assert votd_body["context"]["body"] == "The note the card can show."
    assert daily_body["verse_of_day"]["context"]["body"] == "The note the card can show."


def test_a_model_that_cannot_answer_is_a_503(loaded_client: TestClient, monkeypatch):
    async def failing(**kwargs):
        raise study_ask.StudyAskUnavailable("The model is asleep.")

    monkeypatch.setattr(study_ask, "ask", failing)
    resp = loaded_client.post("/verse-of-day/context", json={})
    assert resp.status_code == 503
    assert "asleep" in resp.json()["detail"]


def test_an_empty_corpus_says_the_verse_is_not_available(client: TestClient, monkeypatch):
    _patch_answer(monkeypatch)
    resp = client.post("/verse-of-day/context", json={})
    assert resp.status_code == 503


def test_the_context_route_requires_the_internal_secret(loaded_client: TestClient):
    resp = loaded_client.post(
        "/verse-of-day/context",
        json={},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert resp.status_code == 403


# the warm-up loop


def _sleeps_until_stopped(monkeypatch, stop_after: int) -> list[int]:
    """Replace the loop's sleep, stopping the otherwise endless task."""
    sleeps: list[int] = []

    async def sleep(seconds: int) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= stop_after:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        verse_context,
        "asyncio",
        SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
    )
    return sleeps


def test_a_failed_warm_up_pass_is_retried_after_a_minute(engine, monkeypatch):
    sleeps = _sleeps_until_stopped(monkeypatch, 1)

    def pick(session):
        raise RuntimeError("the gateway is still restarting")

    monkeypatch.setattr(verse_context.votd, "pick", pick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(verse_context.ensure_daily_loop(lambda: engine, "url", "secret"))
    assert sleeps == [verse_context.RETRY_SECONDS]


def test_repeated_failures_back_off_to_the_normal_interval(engine, monkeypatch):
    sleeps = _sleeps_until_stopped(monkeypatch, 2)

    def pick(session):
        raise RuntimeError("the gateway is still restarting")

    monkeypatch.setattr(verse_context.votd, "pick", pick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(verse_context.ensure_daily_loop(lambda: engine, "url", "secret"))
    assert sleeps == [verse_context.RETRY_SECONDS, verse_context.RETRY_SECONDS * 2]


def test_a_successful_pass_waits_for_the_normal_interval(engine, monkeypatch):
    sleeps = _sleeps_until_stopped(monkeypatch, 1)
    monkeypatch.setattr(verse_context.votd, "pick", lambda session: VERSE)
    monkeypatch.setattr(verse_context, "cached", lambda session, verse: object())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(verse_context.ensure_daily_loop(lambda: engine, "url", "secret"))
    assert sleeps == [verse_context.INTERVAL_SECONDS]
