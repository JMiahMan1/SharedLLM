"""The Bible tool surface: reads only, refusals visible, quotes honest.

Pins three things that would otherwise rot silently:

* the action ``Literal`` is exactly five reads -- no ``delete``, no state or
  mark writes -- because this codebase has no approval gate anywhere in the
  tool path and a reading position belongs to the person holding the phone;
* client refusals name the real problem (a 400 detail like ``Unknown book``,
  a host-networked URL that cannot resolve the Docker service name) instead
  of degrading to an empty answer, per the fail-fast rule;
* a clipped message says so, so a model can never present the first 6,000
  characters of a psalm as the whole passage.
"""

from __future__ import annotations

import json
import typing

import pytest
from pydantic import ValidationError

from services.execution import bible_client
from services.execution.handlers import bible as bible_handler
from services.execution.schemas import BibleRequest, UserContext

PASSAGE = {
    "version": "nkjv",
    "requested": "John 3:16",
    "reference": "John 3:16",
    "spans": 1,
    "count": 1,
    "verses": [
        {
            "version": "nkjv",
            "osis": "John.3.16",
            "book_name": "John",
            "chapter": 3,
            "verse": 16,
            "reference": "John 3:16",
            "text": "For God so loved the world...",
        }
    ],
}
SEARCH_BODY = {
    "version": "nkjv",
    "query": "shepherd",
    "count": 2,
    "results": [
        dict(PASSAGE["verses"][0], reference="Psalm 23:1", text="The Lord is my shepherd."),
        dict(PASSAGE["verses"][0], reference="John 10:11", text="I am the good shepherd."),
    ],
}
NOTES_BODY = {
    "version": "nkjv",
    "edition": "nkjv-tmn",
    "edition_name": "NKJV Study Bible",
    "reference": "Romans 1:16",
    "count": 1,
    "notes": [
        {
            "reference": "Romans 1:16",
            "kind": "commentary",
            "body": "The gospel is God's power saving everyone who believes.",
        }
    ],
}
EMPTY_NOTES = dict(NOTES_BODY, count=0, notes=[], note="No notes for this edition yet.")
VOTD_BODY = {
    "day": "2026-10-07",
    "version": "nkjv",
    "reference": "Psalm 119:105",
    "text": "Your word is a lamp to my feet.",
}
CATALOGUE = {
    "versions": {
        "versions": [
            {"code": "nkjv", "name": "New King James Version", "installed": True},
            {"code": "esv", "name": "English Standard Version", "installed": False},
        ],
        "message": "",
    },
    "editions": {
        "version": "nkjv",
        "editions": [
            {"code": "nkjv-tmn", "name": "NKJV Study Bible", "installed": True, "note_count": 44336},
        ],
    },
    "books": {"version": "nkjv", "books": [{"osis": "Gen", "name": "Genesis"}] * 66},
}


def _resp(payload, status: int = 200) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"status_code": status, "text": text}


class _FakeHttp:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        return self.responses.pop(0)


def _install(monkeypatch, *responses: dict) -> _FakeHttp:
    fake = _FakeHttp(*responses)
    monkeypatch.setattr(bible_client.http_client, "request", fake.request)
    return fake


def _client() -> bible_client.BibleClient:
    return bible_client.BibleClient(
        base_url="http://bible:8010", internal_secret="s3cret"
    )


def _request(action: str, **kwargs) -> BibleRequest:
    return BibleRequest(user_context=UserContext(user="tester"), action=action, **kwargs)


# ---------------------------------------------------------------- client reads


async def test_read_hits_passages_with_ref_and_version(monkeypatch):
    fake = _install(monkeypatch, _resp(PASSAGE))
    body = await _client().read(ref="John 3:16", version="nkjv")
    assert body["reference"] == "John 3:16"
    call = fake.calls[0]
    assert call["url"].endswith("/passages")
    assert call["params"] == {"ref": "John 3:16", "version": "nkjv"}
    assert call["headers"]["X-Internal-Secret"] == "s3cret"


async def test_omitted_optional_params_are_dropped_not_sent_empty(monkeypatch):
    fake = _install(monkeypatch, _resp(PASSAGE))
    await _client().read(ref="Psalm 23")
    assert fake.calls[0]["params"] == {"ref": "Psalm 23"}


async def test_search_sends_query_version_book_and_limit(monkeypatch):
    fake = _install(monkeypatch, _resp(SEARCH_BODY))
    await _client().search(query="shepherd", version="nkjv", book="Psalm", limit=10)
    call = fake.calls[0]
    assert call["url"].endswith("/search")
    assert call["params"] == {"q": "shepherd", "version": "nkjv", "book": "Psalm", "limit": 10}


async def test_cross_version_is_sent_only_when_asked_for(monkeypatch):
    fake = _install(monkeypatch, _resp(NOTES_BODY), _resp(NOTES_BODY))
    await _client().study_notes(ref="Romans 1:16", cross_version=False)
    await _client().study_notes(ref="Romans 1:16", cross_version=True)
    assert "cross_version" not in fake.calls[0]["params"]
    assert fake.calls[1]["params"]["cross_version"] == "true"


async def test_verse_of_day_passes_day_and_scope(monkeypatch):
    fake = _install(monkeypatch, _resp(VOTD_BODY))
    await _client().verse_of_day(day="2026-10-07", version="nkjv", scope="nt")
    call = fake.calls[0]
    assert call["url"].endswith("/verse-of-day")
    assert call["params"] == {"day": "2026-10-07", "version": "nkjv", "scope": "nt"}


async def test_catalogue_fetches_versions_editions_and_books_in_order(monkeypatch):
    fake = _install(
        monkeypatch,
        _resp(CATALOGUE["versions"]),
        _resp(CATALOGUE["editions"]),
        _resp(CATALOGUE["books"]),
    )
    body = await _client().catalogue(version="nkjv")
    paths = [call["url"].rsplit("/", 1)[-1] for call in fake.calls]
    assert paths == ["versions", "editions", "books"]
    assert set(body) == {"versions", "editions", "books"}
    assert body["books"]["version"] == "nkjv"


async def test_a_400_detail_is_a_refusal_quoting_the_service(monkeypatch):
    _install(monkeypatch, _resp({"detail": "Unknown book: Jorb"}, status=400))
    with pytest.raises(bible_client.BibleRefusal, match="Unknown book: Jorb"):
        await _client().search(query="x", book="Jorb")


async def test_a_422_validation_detail_is_flattened_for_the_model(monkeypatch):
    detail = [
        {"type": "string_too_long", "loc": ["body", "ref"], "msg": "String should have at most 2000 characters"}
    ]
    _install(monkeypatch, _resp({"detail": detail}, status=422))
    with pytest.raises(bible_client.BibleRefusal, match="at most 2000 characters"):
        await _client().read(ref="x" * 3000)


async def test_a_500_keeps_its_status_and_detail(monkeypatch):
    _install(monkeypatch, _resp({"detail": "corpus locked"}, status=500))
    with pytest.raises(bible_client.BibleUnavailable, match="answered 500.*corpus locked"):
        await _client().read(ref="John 3:16")


async def test_non_json_is_named_not_parsed(monkeypatch):
    _install(monkeypatch, _resp("<html>proxy error</html>"))
    with pytest.raises(bible_client.BibleUnavailable, match="did not answer with JSON"):
        await _client().read(ref="John 3:16")


async def test_a_transport_failure_says_the_service_could_not_be_reached(monkeypatch):
    async def boom(*args, **kwargs):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(bible_client.http_client, "request", boom)
    with pytest.raises(bible_client.BibleUnavailable, match="could not be reached.*no route"):
        await _client().read(ref="John 3:16")


async def test_a_blank_service_url_is_refused_by_name():
    client = bible_client.BibleClient(base_url="", internal_secret="s3cret")
    with pytest.raises(bible_client.BibleUnavailable, match="bible_svc_url"):
        await client.read(ref="John 3:16")


async def test_a_blank_internal_secret_is_refused_by_name():
    client = bible_client.BibleClient(base_url="http://bible:8010", internal_secret="")
    with pytest.raises(bible_client.BibleUnavailable, match="INTERNAL_SECRET"):
        await client.read(ref="John 3:16")


# ------------------------------------------------------------------- the edge


async def test_the_action_set_is_exactly_the_five_reads():
    annotation = BibleRequest.model_fields["action"].annotation
    assert set(typing.get_args(annotation)) == {
        "read",
        "search",
        "study_notes",
        "votd",
        "catalogue",
    }


async def test_delete_is_not_a_nameable_action():
    with pytest.raises(ValidationError):
        _request("delete")


async def test_write_actions_are_not_nameable_either():
    for action in ("import", "remove", "update_state", "mark"):
        with pytest.raises(ValidationError):
            _request(action)


async def test_scope_is_closed_to_all_ot_nt():
    _request("votd", scope="nt")
    with pytest.raises(ValidationError):
        _request("votd", scope="deuterocanon")


async def test_limit_is_bounded_where_the_service_bounds_it():
    _request("search", q="shepherd", limit=200)
    with pytest.raises(ValidationError):
        _request("search", q="shepherd", limit=201)
    with pytest.raises(ValidationError):
        _request("search", q="shepherd", limit=0)


# --------------------------------------------------------------- the handler


def _stub(monkeypatch, **methods):
    class Stub:
        pass

    stub = Stub()
    for name, fn in methods.items():
        setattr(stub, name, fn)
    monkeypatch.setattr(bible_handler, "_client", lambda: stub)
    return stub


async def test_read_needs_a_ref_and_says_which_field(monkeypatch):
    async def read(*, ref, version=None):
        raise AssertionError("read must not run without a ref")

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read"))
    assert result.status == "FAILURE"
    assert "needs a ref" in result.message


async def test_read_leads_with_reference_version_and_verse_text(monkeypatch):
    async def read(*, ref, version=None):
        return PASSAGE

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read", ref="John 3:16", version="nkjv"))
    assert result.status == "SUCCESS"
    assert result.message.startswith("John 3:16 (nkjv) — 1 verses:")
    assert "John 3:16 For God so loved the world..." in result.message
    assert result.detail["count"] == 1


async def test_an_empty_passage_is_honest_not_blank(monkeypatch):
    async def read(*, ref, version=None):
        return dict(PASSAGE, verses=[], count=0)

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read", ref="Obadiah 1:99"))
    assert result.status == "SUCCESS"
    assert "has no verses" in result.message
    assert "catalogue" in result.message


async def test_search_needs_two_characters_and_names_the_minimum(monkeypatch):
    async def search(*, query, version=None, book=None, limit=50):
        raise AssertionError("search must not run for a one-character query")

    _stub(monkeypatch, search=search)
    result = await bible_handler.handle_bible(_request("search", q="a"))
    assert result.status == "FAILURE"
    assert "at least 2 characters" in result.message


async def test_search_reports_the_count_version_and_verses(monkeypatch):
    async def search(*, query, version=None, book=None, limit=50):
        return SEARCH_BODY

    _stub(monkeypatch, search=search)
    result = await bible_handler.handle_bible(_request("search", q="shepherd"))
    assert result.status == "SUCCESS"
    assert "2 verses match 'shepherd' in nkjv" in result.message
    assert "Psalm 23:1 The Lord is my shepherd." in result.message


async def test_search_with_no_hits_says_so_honestly(monkeypatch):
    async def search(*, query, version=None, book=None, limit=50):
        return {"version": "nkjv", "query": query, "count": 0, "results": []}

    _stub(monkeypatch, search=search)
    result = await bible_handler.handle_bible(_request("search", q="zzzz"))
    assert result.status == "SUCCESS"
    assert "No verses match 'zzzz' in nkjv" in result.message


async def test_study_notes_without_notes_uses_the_service_explanation(monkeypatch):
    async def study_notes(*, ref, version=None, edition=None, kind=None, cross_version=False):
        return EMPTY_NOTES

    _stub(monkeypatch, study_notes=study_notes)
    result = await bible_handler.handle_bible(
        _request("study_notes", ref="Romans 1:16", edition="nkjv-tmn")
    )
    assert result.status == "SUCCESS"
    assert "No notes for this edition yet." in result.message


async def test_study_notes_label_kind_reference_and_edition(monkeypatch):
    async def study_notes(*, ref, version=None, edition=None, kind=None, cross_version=False):
        return NOTES_BODY

    _stub(monkeypatch, study_notes=study_notes)
    result = await bible_handler.handle_bible(_request("study_notes", ref="Romans 1:16"))
    assert result.status == "SUCCESS"
    assert "1 notes from NKJV Study Bible" in result.message
    assert "[commentary] Romans 1:16:" in result.message
    assert result.detail["edition_name"] == "NKJV Study Bible"


async def test_study_notes_without_a_ref_is_refused_by_name(monkeypatch):
    async def study_notes(*, ref, version=None, edition=None, kind=None, cross_version=False):
        raise AssertionError("study_notes must not run without a ref")

    _stub(monkeypatch, study_notes=study_notes)
    result = await bible_handler.handle_bible(_request("study_notes"))
    assert result.status == "FAILURE"
    assert "needs a ref" in result.message


async def test_votd_quotes_reference_version_day_and_text(monkeypatch):
    async def verse_of_day(*, day=None, version=None, scope="all"):
        return VOTD_BODY

    _stub(monkeypatch, verse_of_day=verse_of_day)
    result = await bible_handler.handle_bible(_request("votd"))
    assert result.status == "SUCCESS"
    assert result.message == (
        "Psalm 119:105 (nkjv, 2026-10-07): Your word is a lamp to my feet."
    )


async def test_votd_with_no_text_fails_instead_of_quoting_nothing(monkeypatch):
    async def verse_of_day(*, day=None, version=None, scope="all"):
        return {"day": "2026-10-07", "reference": "", "text": ""}

    _stub(monkeypatch, verse_of_day=verse_of_day)
    result = await bible_handler.handle_bible(_request("votd"))
    assert result.status == "FAILURE"
    assert "returned no text" in result.message


async def test_catalogue_lists_installed_translations_editions_and_book_count(monkeypatch):
    async def catalogue(*, version=None):
        return CATALOGUE

    _stub(monkeypatch, catalogue=catalogue)
    result = await bible_handler.handle_bible(_request("catalogue"))
    assert result.status == "SUCCESS"
    assert "Installed translations: nkjv (New King James Version)" in result.message
    assert "esv" not in result.message  # catalogued but not installed, not listed as installed
    assert "nkjv-tmn (NKJV Study Bible, 44336 notes)" in result.message
    assert "66 books" in result.message
    assert result.detail["installed"] == ["nkjv"]


async def test_a_refusal_becomes_a_visible_failure(monkeypatch):
    async def read(*, ref, version=None):
        raise bible_client.BibleRefusal("Unknown book: Jorb")

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read", ref="Jorb 1:1"))
    assert result.status == "FAILURE"
    assert "Unknown book: Jorb" in result.message


async def test_an_unexpected_crash_is_reported_not_raised(monkeypatch):
    async def read(*, ref, version=None):
        raise RuntimeError("kaboom")

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read", ref="John 3:16"))
    assert result.status == "FAILURE"
    assert "failed unexpectedly" in result.message
    assert "kaboom" in result.message


async def test_an_oversized_passage_is_clipped_with_an_explicit_marker(monkeypatch):
    async def read(*, ref, version=None):
        body = dict(PASSAGE)
        body["verses"] = [
            dict(PASSAGE["verses"][0], text="word " * 3000) for _ in range(5)
        ]
        return body

    _stub(monkeypatch, read=read)
    result = await bible_handler.handle_bible(_request("read", ref="Psalm 119"))
    assert result.status == "SUCCESS"
    assert "Truncated at 6,000 characters" in result.message
    assert len(result.message) < 7000


async def test_host_networking_pointed_at_the_docker_name_fails_fast(monkeypatch):
    from services import config as cfg

    monkeypatch.setattr(cfg, "NETWORK_MODE", "host")
    monkeypatch.setattr(cfg, "BIBLE_SVC_URL", "http://bible:8010")
    result = await bible_handler.handle_bible(_request("read", ref="John 3:16"))
    assert result.status == "FAILURE"
    assert "HOST_BIBLE_SVC_URL" in result.message
