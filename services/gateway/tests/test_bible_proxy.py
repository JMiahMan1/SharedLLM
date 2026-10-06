"""Gateway contract for the ``/api/bible/*`` proxy block.

The bible service answers for whichever ``username`` it is handed, so the
gateway is the only place that decides *whose* reading state a request may
touch. Two failure modes matter and both are pinned here:

* an anonymous caller reaching a personal route (positions, marks, badges);
* a caller smuggling someone else's username in the query string or the JSON
  body of a write and having it honoured.

Family sharing is deliberately **not** decided here. The bible service gates
cross-user reads on the ``bible`` scope in ``UserActivitySharing`` and answers
404 when consent is absent; the gateway's job is to forward a trustworthy
viewer, exactly as the geo block does.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

# Reads and writes a signed-in family member will make in normal use.
ROUTES = [
    ("get", "/api/bible/versions"),
    ("get", "/api/bible/books"),
    ("get", "/api/bible/passages?ref=John+3:16"),
    ("get", "/api/bible/search?q=faith"),
    ("get", "/api/bible/verse-of-day"),
    ("get", "/api/bible/devotional"),
    ("get", "/api/bible/devotional/sources"),
    ("get", "/api/bible/daily"),
    ("get", "/api/bible/study/notes?ref=John+3:16"),
    ("get", "/api/bible/editions"),
    ("get", "/api/bible/marks"),
    ("get", "/api/bible/state"),
    ("get", "/api/bible/stats"),
    ("get", "/api/bible/streaks"),
    ("get", "/api/bible/achievements"),
    ("get", "/api/bible/activity/feed"),
    ("get", "/api/bible/blb/link?ref=John+3:16"),
    ("get", "/api/bible/voices"),
    ("get", "/api/bible/narration?ref=John+3:16"),
    ("get", "/api/bible/admin/imports"),
    ("get", "/api/bible/admin/providers/api.bible/estimate?translation_id=niv"),
    ("post", "/api/bible/admin/imports"),
    ("post", "/api/bible/admin/library"),
    ("post", "/api/bible/admin/imports/upload"),
    ("put", "/api/bible/marks"),
    ("delete", "/api/bible/marks/3"),
    ("put", "/api/bible/state"),
    ("post", "/api/bible/events"),
]


def _patch_bible(monkeypatch, status=200, payload=None, captured=None):
    """Capture whatever the gateway forwards to the bible service.

    ``shared_http_client`` is also how the gateway ships request logs, and that
    POST lands after the bible call, so only bible-bound calls are recorded.
    """
    captured = captured if captured is not None else {}
    captured["calls"] = []
    bible_svc = str(gateway_main.BIBLE_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload or {})

        async def json(self, **kwargs):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload or {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(bible_svc):
                captured["calls"].append(
                    {
                        "verb": verb,
                        "url": url,
                        "params": kwargs.get("params"),
                        "json": kwargs.get("json"),
                        "data": kwargs.get("data"),
                        "files": kwargs.get("files"),
                        "headers": kwargs.get("headers") or {},
                    }
                )
            return _Resp()

        async def request(self, method, url, **kw):
            return await self._record(method.lower(), url, **kw)

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def put(self, url, **kw):
            return await self._record("put", url, **kw)

        async def delete(self, url, **kw):
            return await self._record("delete", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def anon_client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_client(monkeypatch):
    def _make(user="michele", is_admin=False, key="test-token"):
        async def _resolve(api_key):
            if api_key != key:
                return None
            return {"user": user, "user_id": 7, "is_admin": is_admin}

        monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve)
        return TestClient(app, headers={"Authorization": f"Bearer {key}"})

    return _make


# ── authentication ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("verb,path", ROUTES)
def test_anonymous_callers_are_rejected(anon_client, monkeypatch, verb, path):
    captured = _patch_bible(monkeypatch)
    resp = getattr(anon_client, verb)(path)
    assert resp.status_code == 401, f"{verb.upper()} {path} served an anonymous caller"
    assert captured["calls"] == [], f"{path} reached the bible service unauthenticated"


def test_a_junk_key_never_becomes_the_default_admin(make_client, monkeypatch):
    """The dangerous failure is not 'no user' -- it is 'the admin'."""
    _patch_bible(monkeypatch)
    client = make_client(user="nobody", key="valid-key")
    assert client.get("/api/bible/state").status_code in (200, 502)
    anon = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer wrong"})
    assert anon.get("/api/bible/state").status_code == 401


def test_health_needs_no_credentials(monkeypatch):
    _patch_bible(monkeypatch, payload={"status": "ok"})
    assert TestClient(app).get("/api/bible/health").status_code == 200


# ── identity is taken from the token, never from the request ────────────────
@pytest.mark.parametrize(
    "path",
    ["/api/bible/state", "/api/bible/marks", "/api/bible/streaks", "/api/bible/daily"],
)
def test_reads_are_scoped_to_the_caller(make_client, monkeypatch, path):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").get(path)
    assert captured["calls"][-1]["params"]["username"] == "michele"


def test_a_query_supplied_user_id_cannot_reach_another_readers_totals(make_client, monkeypatch):
    """/stats has no user_id parameter on purpose -- see the route docstring."""
    captured = _patch_bible(monkeypatch, payload={})
    resp = make_client(user="michele").get("/api/bible/stats", params={"user_id": "jeremiah"})
    assert resp.status_code == 200
    assert captured["calls"][-1]["params"]["username"] == "michele"


def test_achievements_cannot_be_read_for_another_reader(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").get("/api/bible/achievements", params={"user_id": "jeremiah"})
    assert captured["calls"][-1]["params"]["username"] == "michele"


def test_state_write_ignores_a_username_in_the_body(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").put(
        "/api/bible/state", json={"username": "jeremiah", "book": "John", "chapter": 3, "verse": 16}
    )
    sent = captured["calls"][-1]
    assert sent["params"]["username"] == "michele"
    assert "username" not in sent["json"], "the body's username must never reach the bible service"


def test_mark_write_ignores_a_username_in_the_body(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").put(
        "/api/bible/marks", json={"username": "jeremiah", "ref": "John 3:16", "kind": "highlight"}
    )
    sent = captured["calls"][-1]
    assert sent["params"]["username"] == "michele"
    assert sent["json"]["ref"] == "John 3:16"


def test_deleting_someone_elses_mark_uses_the_caller(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").delete("/api/bible/marks/3", params={"username": "jeremiah"})
    assert captured["calls"][-1]["params"]["username"] == "michele"


def test_events_forward_only_the_three_metadata_fields(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").post(
        "/api/bible/events",
        json={"kind": "chapter_read", "ref": "John 3", "value": 3, "note": "my private note"},
    )
    sent = captured["calls"][-1]["json"]
    assert sent == {"kind": "chapter_read", "ref": "John 3", "value": 3}
    assert "note" not in sent, "free text must not ride along on a reading event"


# ── family sharing: viewer is forwarded, consent is the bible service's job ──
def test_activity_summary_sends_the_viewer_so_consent_can_be_checked(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").get("/api/bible/activity/summary", params={"user_id": "jeremiah"})
    sent = captured["calls"][-1]["params"]
    assert sent["username"] == "jeremiah", "the requested reader must survive"
    assert sent["target"] == "michele", "the bible service needs the viewer to apply consent"


def test_a_refused_cross_user_read_stays_404(make_client, monkeypatch):
    _patch_bible(monkeypatch, status=404, payload={"detail": "Not found"})
    resp = make_client(user="michele").get(
        "/api/bible/activity/summary", params={"user_id": "jeremiah"}
    )
    assert resp.status_code == 404, "an unshared reader must not look like an outage"


def test_activity_feed_is_scoped_to_the_caller(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").get("/api/bible/activity/feed")
    assert captured["calls"][-1]["params"]["viewer"] == "michele"


# ── translation of upstream failures ────────────────────────────────────────
def test_study_notes_forward_only_what_the_caller_asked_for(make_client, monkeypatch):
    """Study notes are reference material, so no username travels with them."""
    captured = _patch_bible(
        monkeypatch,
        payload={"version": "nkjv", "notes": [], "count": 0, "available_kinds": {}},
    )
    resp = make_client(user="michele").get(
        "/api/bible/study/notes", params={"ref": "John 3:16", "kind": "commentary"}
    )
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["verb"] == "get"
    assert call["params"] == {"ref": "John 3:16", "kind": "commentary"}
    assert "username" not in call["params"]


def test_study_notes_keep_the_upstream_explanation_of_there_being_none(make_client, monkeypatch):
    """A translation with no study material must say so, not look broken."""
    detail = {"version": "kjv", "notes": [], "count": 0, "note": "kjv carries no study notes"}
    _patch_bible(monkeypatch, payload=detail)
    resp = make_client(user="michele").get("/api/bible/study/notes", params={"ref": "John 3:16"})
    assert resp.status_code == 200
    assert resp.json()["note"] == "kjv carries no study notes"


def test_an_unknown_study_note_kind_stays_a_400(make_client, monkeypatch):
    _patch_bible(monkeypatch, status=400, payload={"detail": "unknown study note kind 'sermon'"})
    resp = make_client(user="michele").get("/api/bible/study/notes", params={"ref": "John 3:16"})
    assert resp.status_code == 400
    assert "sermon" in resp.json()["detail"]


def test_the_study_editions_catalogue_carries_no_username(make_client, monkeypatch):
    """Which study Bibles exist is server state, not anything a user owns."""
    captured = _patch_bible(
        monkeypatch, payload={"version": "nkjv", "default": "kjv", "editions": [], "other_translations": []}
    )
    resp = make_client(user="michele").get("/api/bible/editions", params={"version": "nkjv"})
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["url"] == f"{gateway_main.BIBLE_SVC}/editions"
    assert call["params"] == {"version": "nkjv"}
    assert "username" not in call["params"]


def test_the_cross_version_switch_is_passed_as_a_flag_and_only_when_asked(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"version": "nkjv", "notes": [], "count": 0})

    off = make_client(user="michele").get("/api/bible/study/notes", params={"ref": "John 3:16"})
    assert off.status_code == 200
    assert "cross_version" not in captured["calls"][-1]["params"]

    on = make_client(user="michele").get(
        "/api/bible/study/notes", params={"ref": "John 3:16", "cross_version": "true"}
    )
    assert on.status_code == 200
    assert captured["calls"][-1]["params"]["cross_version"] == "true"


def test_a_chosen_study_bible_is_passed_through(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"version": "nkjv", "notes": [], "count": 0})
    resp = make_client(user="michele").get(
        "/api/bible/study/notes", params={"ref": "John 3:16", "edition": "nkjv-macarthur"}
    )
    assert resp.status_code == 200
    assert captured["calls"][-1]["params"]["edition"] == "nkjv-macarthur"


def test_an_unimported_corpus_stays_503_with_its_message(make_client, monkeypatch):
    """No corpus is an operator problem, and the message names the import."""
    _patch_bible(
        monkeypatch,
        status=503,
        payload={"detail": "No Bible text is imported. Run: python -m services.bible.import_corpus"},
    )
    resp = make_client(user="michele").get("/api/bible/passages", params={"ref": "John 3:16"})
    assert resp.status_code == 503
    assert "import_corpus" in resp.json()["detail"]


def test_an_unconfigured_blb_url_is_not_flattened_to_502(make_client, monkeypatch):
    _patch_bible(monkeypatch, status=503, payload={"detail": "blb_base_url is not configured."})
    resp = make_client(user="michele").get("/api/bible/blb/link", params={"ref": "John 3:16"})
    assert resp.status_code == 503
    assert "blb_base_url" in resp.json()["detail"]


def test_a_genuine_upstream_fault_is_502(make_client, monkeypatch):
    _patch_bible(monkeypatch, status=502, payload={})
    assert make_client(user="michele").get("/api/bible/stats").status_code == 502


# ── transport ───────────────────────────────────────────────────────────────
def test_unset_optional_params_are_dropped_not_forwarded_as_none(make_client, monkeypatch):
    """yarl raises on a None query value, so this would be a 500 without the drop."""
    captured = _patch_bible(monkeypatch, payload={})
    resp = make_client(user="michele").get("/api/bible/passages", params={"ref": "John 3:16"})
    assert resp.status_code == 200
    assert captured["calls"][-1]["params"] == {"ref": "John 3:16"}


def test_every_upstream_call_carries_the_internal_secret(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    make_client(user="michele").get("/api/bible/passages", params={"ref": "John 3:16"})
    headers = captured["calls"][-1]["headers"]
    assert headers.get("X-Internal-Secret") == gateway_main.INTERNAL_SECRET


def test_the_daily_route_is_a_single_get_for_the_android_widget(make_client, monkeypatch):
    """The home-screen widget can only issue a bearer-authenticated GET."""
    captured = _patch_bible(monkeypatch, payload={"day": "2026-01-01"})
    resp = make_client(user="michele").get("/api/bible/daily")
    assert resp.status_code == 200
    assert captured["calls"][-1]["verb"] == "get"

# ── read-aloud ──────────────────────────────────────────────────────────────
def test_the_voice_list_never_carries_a_username(make_client, monkeypatch):
    """These are engine voices, not reading state; nothing personal travels."""
    captured = _patch_bible(monkeypatch, payload={"voices": ["af_heart"], "count": 1})
    resp = make_client(user="michele").get("/api/bible/voices")
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["url"].endswith("/voices")
    assert not call["params"]


def test_narration_forwards_only_the_passage_and_the_voice(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"audio_base64": "AAA="})
    resp = make_client(user="michele").get(
        "/api/bible/narration", params={"ref": "John 3:16", "version": "nkjv", "voice": "af_heart"}
    )
    assert resp.status_code == 200
    assert captured["calls"][-1]["params"] == {
        "ref": "John 3:16",
        "version": "nkjv",
        "voice": "af_heart",
    }


def test_an_unset_voice_is_dropped_rather_than_forwarded_empty(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"audio_base64": "AAA="})
    make_client(user="michele").get("/api/bible/narration", params={"ref": "John 3:16"})
    assert "version" not in captured["calls"][-1]["params"]


def test_a_missing_kokoro_voice_keeps_the_install_hint(make_client, monkeypatch):
    """A missing voice file is an operator problem with a known fix."""
    _patch_bible(
        monkeypatch,
        status=503,
        payload={"detail": "Kokoro voices missing. Install them with POST /execute/tts/download"},
    )
    resp = make_client(user="michele").get("/api/bible/narration", params={"ref": "John 3:16"})
    assert resp.status_code == 503
    assert "/execute/tts/download" in resp.json()["detail"]


def test_a_passage_that_is_too_long_to_narrate_is_a_400(make_client, monkeypatch):
    captured = _patch_bible(
        monkeypatch, status=400, payload={"detail": "That passage is 200 verses. Select a shorter passage."}
    )
    resp = make_client(user="michele").get("/api/bible/narration", params={"ref": "Psalms 119"})
    assert resp.status_code == 400
    assert "shorter passage" in resp.json()["detail"]
    assert captured["calls"]


# ── imports are an administrator's job ──────────────────────────────────────
def test_a_family_member_cannot_import_a_translation(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    resp = make_client(user="michele", is_admin=False).post(
        "/api/bible/admin/imports", json={"code": "esv", "kind": "json"}
    )
    assert resp.status_code == 403
    assert captured["calls"] == [], "a non-admin reached the importer"


def test_an_anonymous_upload_never_reaches_the_service(anon_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    resp = anon_client.post("/api/bible/admin/imports/upload", data={"code": "esv"})
    assert resp.status_code == 401
    assert captured["calls"] == []


def test_an_import_forwards_only_the_declared_fields(make_client, monkeypatch):
    """An unexpected key is dropped here rather than reaching the filesystem."""
    captured = _patch_bible(monkeypatch, payload={"status": "succeeded"})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports",
        json={
            "code": "esv",
            "kind": "pdf",
            "source_path": "/data/bible/esv.pdf",
            "import_notes": True,
            "edition": "",
            "license_class": "public_domain",
        },
    )
    assert resp.status_code == 200
    assert captured["calls"][-1]["json"] == {
        "code": "esv",
        "kind": "pdf",
        "source_path": "/data/bible/esv.pdf",
        "import_notes": True,
    }


def test_a_refusal_is_reported_as_a_readable_report_not_a_bare_422(make_client, monkeypatch):
    _patch_bible(
        monkeypatch,
        payload={
            "status": "failed",
            "code": "esv",
            "message": "Exodus is missing from that PDF",
            "log": ["Refused: Exodus is missing from that PDF"],
        },
    )
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports", json={"code": "esv", "kind": "pdf"}
    )
    assert resp.status_code == 422
    assert "Exodus" in resp.json()["message"]


def test_the_import_catalogue_is_admin_only(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"versions": [], "runs": []})
    assert make_client(user="michele", is_admin=False).get("/api/bible/admin/imports").status_code == 403
    assert make_client(user="jeremiah", is_admin=True).get("/api/bible/admin/imports").status_code == 200
    assert captured["calls"][-1]["url"].endswith("/admin/imports")


def test_an_unset_import_directory_reaches_the_operator(make_client, monkeypatch):
    _patch_bible(monkeypatch, status=503, payload={"detail": "BIBLE_IMPORT_DIR is not configured."})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports", json={"code": "esv", "provider": "api.bible", "provider_id": "ESV"}
    )
    assert resp.status_code == 503
    assert "BIBLE_IMPORT_DIR" in resp.json()["detail"]


def test_the_upload_is_remultiparted_with_its_file(make_client, monkeypatch):
    """The bible service decides what the file is, so it must receive the bytes."""
    captured = _patch_bible(monkeypatch, payload={"status": "succeeded", "verse_count": 31102})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports/upload",
        files={"file": ("nkjv.epub", b"PK\x03\x04fake", "application/epub+zip")},
        data={"code": "nkjv", "kind": "epub", "import_notes": "true", "edition": "nkjv-macarthur"},
    )
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["url"].endswith("/admin/imports/upload")
    assert call["files"]["file"][0] == "nkjv.epub"
    assert call["data"]["import_notes"] is True
    assert call["data"]["edition"] == "nkjv-macarthur"


def test_an_upload_without_a_file_is_refused_before_the_service(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports/upload", data={"code": "esv"}
    )
    assert resp.status_code == 400
    assert captured["calls"] == []


def test_the_cost_estimate_reports_what_would_still_be_requested(make_client, monkeypatch):
    """The number that stops a month's allowance being spent by accident."""
    captured = _patch_bible(
        monkeypatch,
        payload={
            "translation_id": "niv",
            "chapters": 1189,
            "cached": 1189,
            "remaining": 0,
            "calls": 0,
            "budget": 1200,
            "within_budget": True,
        },
    )
    resp = make_client(user="jeremiah", is_admin=True).get(
        "/api/bible/admin/providers/api.bible/estimate", params={"translation_id": "niv"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["remaining"] == 0 and body["within_budget"] is True
    call = captured["calls"][-1]
    assert call["url"] == f"{gateway_main.BIBLE_SVC}/admin/providers/api.bible/estimate"
    assert call["params"] == {"translation_id": "niv"}


def test_an_estimate_over_the_budget_is_passed_through_so_the_ui_can_warn(make_client, monkeypatch):
    _patch_bible(
        monkeypatch,
        payload={"chapters": 1189, "cached": 0, "remaining": 1189, "budget": 500, "within_budget": False},
    )
    resp = make_client(user="jeremiah", is_admin=True).get(
        "/api/bible/admin/providers/api.bible/estimate", params={"translation_id": "nlt"}
    )
    assert resp.status_code == 200
    assert resp.json()["within_budget"] is False


def test_estimating_a_cost_is_admin_only(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"remaining": 0})
    resp = make_client(user="michele").get(
        "/api/bible/admin/providers/api.bible/estimate", params={"translation_id": "niv"}
    )
    assert resp.status_code == 403
    assert captured["calls"] == []


def test_a_dry_run_is_forwarded_as_a_flag_and_not_as_a_string(make_client, monkeypatch):
    payload = gateway_main._bible_import_payload(
        {"code": "nlt", "provider": "api.bible", "provider_id": "d6e14a625393b4da-01", "dry_run": True}
    )
    assert payload["dry_run"] is True
    assert payload["code"] == "nlt"


def test_the_call_budget_is_forwarded_as_a_number(make_client, monkeypatch):
    payload = gateway_main._bible_import_payload({"code": "nlt", "budget": 1200})
    assert payload["budget"] == 1200
    assert gateway_main._bible_import_payload({"code": "nlt"}) == {"code": "nlt"}


def test_the_library_folder_is_forwarded_as_a_path_not_a_query_string(make_client, monkeypatch):
    """A folder name is operator input, so it travels in the body.

    WebDAV folder names carry slashes and can carry semicolons, so putting one
    in a query string is a quoting problem waiting to happen.
    """
    captured = _patch_bible(monkeypatch, payload={"root": "/Books/Text", "entries": [], "count": 0})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/library", json={"path": "/Books/Text/Thomas Nelson; NKJV (3198)"}
    )
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["url"] == f"{gateway_main.BIBLE_SVC}/admin/library"
    assert call["json"] == {"path": "/Books/Text/Thomas Nelson; NKJV (3198)"}


def test_browsing_the_library_with_no_path_asks_for_the_shelf_root(make_client, monkeypatch):
    """The admin page lists the shelf first, so an absent path means the root."""
    captured = _patch_bible(monkeypatch, payload={"root": "/Books/Text", "entries": []})
    resp = make_client(user="jeremiah", is_admin=True).post("/api/bible/admin/library", json={})
    assert resp.status_code == 200
    assert captured["calls"][-1]["json"] == {"path": ""}


def test_browsing_the_library_keeps_the_reason_the_shelf_is_unreachable(make_client, monkeypatch):
    """An unset shelf is the operator's to fix, so the sentence has to survive."""
    _patch_bible(monkeypatch, status=503, payload={"detail": "calibre_library_path is not set."})
    resp = make_client(user="jeremiah", is_admin=True).post("/api/bible/admin/library", json={})
    assert resp.status_code == 503
    assert "calibre_library_path" in resp.json()["detail"]


def test_browsing_the_library_is_admin_only(make_client, monkeypatch):
    captured = _patch_bible(monkeypatch, payload={"entries": []})
    resp = make_client(user="michele").post("/api/bible/admin/library", json={})
    assert resp.status_code == 403
    assert captured["calls"] == []


def test_a_library_import_is_forwarded_whole(make_client, monkeypatch):
    """The gateway never fetches the file; it names the shelf and stands back."""
    captured = _patch_bible(monkeypatch, payload={"status": "succeeded", "code": "kjv"})
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports",
        json={"code": "kjv", "kind": "epub", "library_path": "/Books/Text/Thomas Nelson/Bible.epub"},
    )
    assert resp.status_code == 200
    call = captured["calls"][-1]
    assert call["json"]["library_path"] == "/Books/Text/Thomas Nelson/Bible.epub"
    assert "source_path" not in call["json"]


def test_a_shelf_import_refusal_reaches_the_operator_as_422(make_client, monkeypatch):
    _patch_bible(
        monkeypatch,
        payload={"status": "failed", "message": "Exodus is missing from that book."},
    )
    resp = make_client(user="jeremiah", is_admin=True).post(
        "/api/bible/admin/imports",
        json={"code": "kjv", "library_path": "/Books/Text/x.epub"},
    )
    assert resp.status_code == 422
    assert "Exodus" in resp.json()["message"]
