"""Study help: the question is grounded in what the reader can see, or refused.

Two layers are pinned here. ``build_prompt`` decides what the model is shown --
the passage and its study notes and nothing else -- and the limits are refusals
rather than truncations, because an answer built from quietly shortened
Scripture reads as if the whole passage had been considered. ``ask`` is the
conversation with the gateway, stubbed at the HTTP boundary the same way the
narration tests stub the speech engine, and every failure mode is asserted to
arrive as a named sentence rather than as an empty answer.
"""

import json

import pytest
from fastapi.testclient import TestClient

from services.bible import study_ask

pytestmark = pytest.mark.unit


VERSE = {
    "reference": "John 3:16",
    "text": "For God so loved the world, that he gave his only begotten Son.",
}
NOTE = {
    "reference": "John 3:16",
    "kind": "commentary",
    "body": "The word translated 'world' is kosmos, the ordered creation.",
}


# ── what the model is shown ────────────────────────────────────────────────


def test_the_prompt_carries_the_passage_and_its_notes():
    prompt = study_ask.build_prompt(
        reference="John 3:16",
        verses=[VERSE],
        notes=[NOTE],
        question="What does 'world' mean here?",
    )
    assert prompt.startswith("Passage: John 3:16")
    assert "Scripture:" in prompt
    assert "John 3:16 For God so loved the world" in prompt
    assert "Study notes for this passage:" in prompt
    assert "John 3:16 (commentary): The word translated 'world' is kosmos" in prompt
    assert "Question:\nWhat does 'world' mean here?" in prompt
    # The closing instruction is what makes "it does not say" an acceptable
    # answer instead of a failure the model tries to paper over.
    assert "If it does not answer the question, say so." in prompt


def test_a_question_is_separated_from_the_scripture_it_is_about():
    prompt = study_ask.build_prompt(
        reference="John 3:16",
        verses=[VERSE],
        notes=[],
        question="Why does this matter?",
    )
    assert "Study notes for this passage:" not in prompt
    assert prompt.index("Scripture:") < prompt.index("Question:")


def test_blank_verses_and_notes_are_left_out_rather_than_shown_empty():
    prompt = study_ask.build_prompt(
        reference="John 3:16",
        verses=[VERSE, {"reference": "John 3:17", "text": "   "}],
        notes=[NOTE, {"reference": "John 3:17", "kind": "footnote", "body": ""}],
        question="What does this mean?",
    )
    assert "John 3:17" not in prompt


def test_a_very_long_note_is_shortened_visibly():
    long_body = "x" * (study_ask.MAX_NOTE_CHARACTERS + 500)
    prompt = study_ask.build_prompt(
        reference="John 3:16",
        verses=[VERSE],
        notes=[{"reference": "John 3:16", "kind": "commentary", "body": long_body}],
        question="What does this mean?",
    )
    assert " ..." in prompt
    assert long_body not in prompt


def test_only_the_first_notes_are_shown_and_the_limit_is_a_number():
    notes = [
        {"reference": f"John 3:{n}", "kind": "footnote", "body": f"note {n}"}
        for n in range(1, study_ask.MAX_NOTES + 20)
    ]
    prompt = study_ask.build_prompt(
        reference="John 3",
        verses=[VERSE],
        notes=notes,
        question="What does this mean?",
    )
    assert f"note {study_ask.MAX_NOTES}" in prompt
    assert f"note {study_ask.MAX_NOTES + 1}" not in prompt


@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_is_refused(question):
    with pytest.raises(study_ask.StudyAskError) as exc:
        study_ask.build_prompt(
            reference="John 3:16", verses=[VERSE], notes=[], question=question
        )
    assert "Ask a question" in str(exc.value)


def test_an_over_long_question_is_refused_and_says_the_limit():
    with pytest.raises(study_ask.StudyAskError) as exc:
        study_ask.build_prompt(
            reference="John 3:16",
            verses=[VERSE],
            notes=[],
            question="x" * (study_ask.MAX_QUESTION_CHARACTERS + 1),
        )
    message = str(exc.value)
    assert str(study_ask.MAX_QUESTION_CHARACTERS) in message
    assert "shorter" in message


def test_an_over_long_passage_is_refused_rather_than_shortened():
    verses = [{"reference": f"Psalm 119:{n}", "text": "line"} for n in range(1, 200)]
    with pytest.raises(study_ask.StudyAskError) as exc:
        study_ask.build_prompt(
            reference="Psalm 119", verses=verses, notes=[], question="What is this?"
        )
    message = str(exc.value)
    assert str(len(verses)) in message
    assert str(study_ask.MAX_VERSES) in message


def test_a_passage_with_no_text_is_refused():
    with pytest.raises(study_ask.StudyAskError) as exc:
        study_ask.build_prompt(
            reference="John 3:16",
            verses=[{"reference": "John 3:16", "text": ""}],
            notes=[],
            question="What does this mean?",
        )
    assert "no text" in str(exc.value)


# ── reading the answer out of whatever shape comes back ────────────────────


def test_an_openai_reply_yields_its_content():
    data = {"choices": [{"message": {"content": "  It means love.  "}}]}
    assert study_ask._extract_answer(data) == "It means love."


@pytest.mark.parametrize(
    "data",
    [
        {"response": "The gateway's own shape."},
        {"answer": "The gateway's own shape."},
        {"message": {"content": "The gateway's own shape."}},
    ],
)
def test_a_gateway_native_reply_is_read_too(data):
    assert study_ask._extract_answer(data) == "The gateway's own shape."


@pytest.mark.parametrize("data", [None, [], {}, {"choices": []}, {"response": "   "}])
def test_an_empty_or_unknown_reply_yields_nothing(data):
    assert study_ask._extract_answer(data) == ""


# ── the conversation with the gateway ──────────────────────────────────────


class _Response:
    """The aiohttp response surface ``ask`` touches."""

    def __init__(self, status: int, body: object):
        self.status = status
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, response: _Response | None, *, boom: Exception | None = None):
        self._response = response
        self._boom = boom
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json or {}, headers or {}))
        if self._boom is not None:
            raise self._boom
        if self._response is None:
            raise AssertionError("unexpected call to the gateway")
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch(monkeypatch, session: _Session):
    monkeypatch.setattr(study_ask.aiohttp, "ClientSession", lambda **kwargs: session)
    return session


def _answer(text: str = "It is about love.") -> _Response:
    return _Response(200, {"choices": [{"message": {"content": text}}]})


async def test_the_question_goes_to_the_gateway_with_thinking_turned_off(monkeypatch):
    session = _patch(monkeypatch, _Session(_answer()))
    result = await study_ask.ask(
        prompt="Passage: John 3:16\n\nQuestion: Why?",
        user="jeremiah",
        gateway_url="http://gateway:11435",
        internal_secret="s3cret",
    )
    assert result == "It is about love."
    url, body, headers = session.calls[0]
    assert url == "http://gateway:11435/v1/chat/completions"
    assert body["model"] == "assistant"
    assert body["rag_user"] == "jeremiah"
    assert body["think"] is False
    assert body["enable_thinking"] is False
    assert body["messages"][0]["role"] == "system"
    assert "say so plainly" in body["messages"][0]["content"]
    assert body["messages"][1]["content"].startswith("Passage: John 3:16")
    assert headers == {"X-Internal-Secret": "s3cret"}


async def test_a_trailing_slash_on_the_gateway_url_does_not_double_up(monkeypatch):
    session = _patch(monkeypatch, _Session(_answer()))
    await study_ask.ask(
        prompt="p", user="jeremiah", gateway_url="http://gateway:11435/", internal_secret=""
    )
    assert session.calls[0][0] == "http://gateway:11435/v1/chat/completions"


async def test_an_unset_gateway_url_names_the_setting(monkeypatch):
    _patch(monkeypatch, _Session(None))
    with pytest.raises(study_ask.StudyAskUnavailable) as exc:
        await study_ask.ask(prompt="p", user="jeremiah", gateway_url="", internal_secret="")
    assert "GATEWAY_INTERNAL_URL" in str(exc.value)


async def test_a_gateway_error_keeps_the_gateways_own_words(monkeypatch):
    _patch(monkeypatch, _Session(_Response(503, {"detail": "model is loading"})))
    with pytest.raises(study_ask.StudyAskUnavailable) as exc:
        await study_ask.ask(
            prompt="p", user="jeremiah", gateway_url="http://gateway:11435", internal_secret=""
        )
    assert "503" in str(exc.value)
    assert "model is loading" in str(exc.value)


async def test_a_reply_that_is_not_json_is_named(monkeypatch):
    _patch(monkeypatch, _Session(_Response(200, ValueError("not json"))))
    with pytest.raises(study_ask.StudyAskUnavailable) as exc:
        await study_ask.ask(
            prompt="p", user="jeremiah", gateway_url="http://gateway:11435", internal_secret=""
        )
    assert "not JSON" in str(exc.value)


async def test_an_unreachable_gateway_is_reported_not_swallowed(monkeypatch):
    boom = study_ask.aiohttp.ClientConnectionError("no route to gateway")
    _patch(monkeypatch, _Session(None, boom=boom))
    with pytest.raises(study_ask.StudyAskUnavailable) as exc:
        await study_ask.ask(
            prompt="p", user="jeremiah", gateway_url="http://gateway:11435", internal_secret=""
        )
    assert "could not reach" in str(exc.value)


async def test_an_empty_answer_is_a_failure_not_an_empty_string(monkeypatch):
    _patch(monkeypatch, _Session(_Response(200, {"choices": [{"message": {"content": "  "}}]})))
    with pytest.raises(study_ask.StudyAskUnavailable) as exc:
        await study_ask.ask(
            prompt="p", user="jeremiah", gateway_url="http://gateway:11435", internal_secret=""
        )
    assert "empty answer" in str(exc.value)


@pytest.mark.parametrize("failure", ["http", "empty", "transport"])
async def test_no_failure_becomes_an_answer(monkeypatch, failure):
    if failure == "http":
        session = _Session(_Response(500, {"message": "boom"}))
    elif failure == "empty":
        session = _Session(_Response(200, {"response": ""}))
    else:
        session = _Session(None, boom=study_ask.aiohttp.ClientError("down"))
    _patch(monkeypatch, session)
    with pytest.raises(study_ask.StudyAskUnavailable):
        await study_ask.ask(
            prompt="p", user="jeremiah", gateway_url="http://gateway:11435", internal_secret=""
        )


# ── the route, end to end against a stubbed gateway ────────────────────────


def _patch_gateway(monkeypatch, answer: str = "It is about love."):
    session = _Session(_answer(answer))
    monkeypatch.setattr(study_ask.aiohttp, "ClientSession", lambda **kwargs: session)
    return session


def test_asking_about_a_passage_returns_a_grounded_answer(
    loaded_client: TestClient, reader_db, monkeypatch
):
    _patch_gateway(monkeypatch, "God's love for the whole world.")
    resp = loaded_client.post(
        "/study/ask?username=jeremiah",
        json={"ref": "Gen 1:1", "question": "What is being created?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "God's love for the whole world."
    assert body["reference"] == "Genesis 1:1"
    assert body["version"] == "kjv"
    assert body["question"] == "What is being created?"
    assert body["verses_used"] >= 1
    # The public-domain mini corpus has no study notes, and the count says so
    # rather than the answer pretending otherwise.
    assert body["notes_used"] == 0


def test_the_question_is_recorded_but_not_its_text(
    loaded_client: TestClient, reader_db, monkeypatch
):
    from sqlmodel import select

    from services.bible.models import ReadingEvent

    _patch_gateway(monkeypatch)
    loaded_client.post(
        "/study/ask?username=jeremiah",
        json={"ref": "Gen 1:1", "question": "What secret thing did I ask?"},
    )
    rows = reader_db.exec(select(ReadingEvent).where(ReadingEvent.kind == "assistant_ask")).all()
    assert len(rows) == 1
    assert rows[0].ref == "Genesis 1:1"
    assert rows[0].value == 0
    # Reading events are metadata only: the row has nowhere to keep the
    # question, so none of the reader's words can end up in the table.
    assert {"kind", "ref", "value", "day"} <= {c.name for c in ReadingEvent.__table__.columns}


def test_a_bad_reference_is_the_readers_mistake(loaded_client: TestClient, monkeypatch):
    session = _patch_gateway(monkeypatch)
    resp = loaded_client.post(
        "/study/ask?username=jeremiah", json={"ref": "Foo 1:1", "question": "What is this?"}
    )
    assert resp.status_code == 400
    assert session.calls == []


def test_an_empty_question_is_refused_before_the_model_is_called(
    loaded_client: TestClient, monkeypatch
):
    session = _patch_gateway(monkeypatch)
    resp = loaded_client.post(
        "/study/ask?username=jeremiah", json={"ref": "Gen 1:1", "question": "   "}
    )
    assert resp.status_code == 400
    assert "Ask a question" in resp.json()["detail"]
    assert session.calls == []


def test_a_model_that_cannot_answer_is_a_503_not_an_empty_answer(
    loaded_client: TestClient, monkeypatch
):
    monkeypatch.setattr(
        study_ask.aiohttp,
        "ClientSession",
        lambda **kwargs: _Session(None, boom=study_ask.aiohttp.ClientError("gateway down")),
    )
    resp = loaded_client.post(
        "/study/ask?username=jeremiah", json={"ref": "Gen 1:1", "question": "Why?"}
    )
    assert resp.status_code == 503
    assert "could not reach" in resp.json()["detail"]


def test_an_unimported_translation_is_refused(loaded_client: TestClient, monkeypatch):
    session = _patch_gateway(monkeypatch)
    resp = loaded_client.post(
        "/study/ask?username=jeremiah",
        json={"ref": "Gen 1:1", "question": "Why?", "version": "nkjv"},
    )
    assert resp.status_code == 400
    assert "nkjv" in resp.json()["detail"]
    assert session.calls == []


def test_the_route_requires_the_internal_secret(loaded_client: TestClient):
    resp = loaded_client.post(
        "/study/ask?username=jeremiah",
        json={"ref": "Gen 1:1", "question": "Why?"},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert resp.status_code == 403


def test_the_route_needs_a_question(loaded_client: TestClient):
    resp = loaded_client.post("/study/ask?username=jeremiah", json={"ref": "Gen 1:1"})
    assert resp.status_code == 422
