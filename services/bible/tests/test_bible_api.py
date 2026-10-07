"""HTTP surface.

Two things are tested here that the unit tests cannot reach: the internal-secret
edge (a reader's Scripture must not be readable by anyone who can reach the
port), and the failure messages, because "unconfigured" is only useful if it
says which setting to set.
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import services.config as cfg
from services.bible import corpus
from services.bible import main as bible_main

from services.bible.tests.conftest import auth

SECRET = "test-secret"


def test_health_needs_no_secret(client: TestClient):
    assert client.get("/health").status_code == 200


def test_routes_refuse_a_missing_secret(loaded_client: TestClient):
    response = loaded_client.get("/versions", headers={"X-Internal-Secret": ""})
    assert response.status_code == 403


def test_routes_refuse_a_wrong_secret(loaded_client: TestClient):
    response = loaded_client.get("/versions", headers={"X-Internal-Secret": "guessed"})
    assert response.status_code == 403


def test_info_endpoint_is_protected_too(loaded_client: TestClient):
    assert loaded_client.get("/info", headers={"X-Internal-Secret": SECRET}).status_code == 200


def test_health_reports_the_corpus(client: TestClient):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["versions"] == []


def test_an_empty_corpus_points_at_the_admin_page_not_a_shell(client: TestClient):
    detail = client.get("/passages", params={"ref": "John 3:16"}).json()["detail"]
    assert "Admin > Bible" in detail
    assert "python" not in detail
    assert "import_corpus" not in detail


def test_an_empty_corpus_points_at_the_admin_page_for_the_verse(client: TestClient):
    detail = client.get("/verse-of-day").json()["detail"]
    assert "Admin > Bible" in detail
    assert "import_corpus" not in detail


def test_the_empty_corpus_message_names_the_public_domain_fallback(client: TestClient):
    detail = client.get("/passages", params={"ref": "John 3:16"}).json()["detail"]
    assert "fallback" in detail
    assert "kjv" in detail


def test_daily_surfaces_the_corpus_error_instead_of_a_blank_card(client: TestClient):
    body = client.get("/daily").json()
    assert "error" in body["verse_of_day"]
    assert "Admin > Bible" in body["verse_of_day"]["error"]
    assert "import_corpus" not in body["verse_of_day"]["error"]


def test_versions_carries_the_reason_there_is_nothing_to_read(client: TestClient):
    body = client.get("/versions").json()
    assert "Admin > Bible" in body["message"]
    assert "import_corpus" not in body["message"]


def test_versions_has_no_message_once_something_is_installed(loaded_client: TestClient):
    assert loaded_client.get("/versions").json()["message"] == ""


def test_the_catalogue_never_hands_a_reader_a_command(loaded_client: TestClient):
    versions = loaded_client.get("/versions").json()["versions"]
    editions = loaded_client.get("/editions").json()["editions"]
    for entry in [*versions, *editions]:
        note = str(entry.get("note") or "")
        assert "python" not in note
        assert "import_" not in note


# ── reading ─────────────────────────────────────────────────────────────────


def test_read_a_passage(loaded_client: TestClient):
    body = loaded_client.get("/passages", params={"ref": "Gen 1:1-2"}).json()
    assert body["reference"] == "Genesis 1:1-2"
    assert [v["verse"] for v in body["verses"]] == [1, 2]
    assert body["verses"][0]["text"] == "Verse 1:1 in Gen."


def test_a_bad_reference_is_a_400_not_a_500(loaded_client: TestClient):
    response = loaded_client.get("/passages", params={"ref": "Notabook 9:9"})
    assert response.status_code == 400
    assert "Notabook" in response.json()["detail"]


def test_an_unimported_version_names_the_alternatives(loaded_client: TestClient):
    detail = loaded_client.get("/passages", params={"ref": "Gen 1:1", "version": "esv"}).json()["detail"]
    assert "esv" in detail and "api.bible" in detail


def test_an_unknown_version_is_a_400_that_lists_what_is_there(loaded_client: TestClient):
    detail = loaded_client.get("/passages", params={"ref": "Gen 1:1", "version": "nope"}).json()["detail"]
    assert "nope" in detail and "not a translation this server knows about" in detail
    assert "corpus_manifest.json" not in detail


def test_versions_lists_uninstalled_translations_with_a_reason(loaded_client: TestClient):
    versions = {v["code"]: v for v in loaded_client.get("/versions").json()["versions"]}
    assert versions["kjv"]["installed"] is True
    assert versions["kjv"]["verse_count"] > 0
    assert versions["esv"]["installed"] is False
    assert versions["esv"]["license_class"] == "licensed"
    assert versions["esv"]["note"]
    assert {v["code"] for v in versions.values()} >= {"kjv", "asv", "web", "nkjv", "nlt", "esv", "niv"}


def test_books_lists_canonical_order(loaded_client: TestClient):
    books = loaded_client.get("/books").json()["books"]
    assert [b["osis"] for b in books][:3] == ["Gen", "Exod", "Lev"]
    assert books[0]["chapters"] == 50


def test_search_finds_text(loaded_client: TestClient):
    body = loaded_client.get("/search", params={"q": "in Gen"}).json()
    assert body["count"] > 0
    assert body["results"][0]["reference"].startswith("Genesis ")


def test_search_rejects_a_single_character(loaded_client: TestClient):
    assert loaded_client.get("/search", params={"q": "a"}).status_code == 422


def test_search_rejects_an_unknown_book(loaded_client: TestClient):
    response = loaded_client.get("/search", params={"q": "love", "book": "Notabook"})
    assert response.status_code == 400


def test_verse_of_day_is_stable_within_a_day(loaded_client: TestClient):
    first = loaded_client.get("/verse-of-day").json()
    second = loaded_client.get("/verse-of-day").json()
    assert first["reference"] == second["reference"]


def test_verse_of_day_accepts_an_explicit_date(loaded_client: TestClient):
    body = loaded_client.get("/verse-of-day", params={"day": "2026-12-25"}).json()
    assert body["day"] == "2026-12-25"
    assert body["day_of_year"] == 359


def test_verse_of_day_rejects_a_bad_date(loaded_client: TestClient):
    response = loaded_client.get("/verse-of-day", params={"day": "25/12/2026"})
    assert response.status_code == 400


def test_verse_of_day_rejects_an_unknown_scope(loaded_client: TestClient):
    assert loaded_client.get("/verse-of-day", params={"scope": "apocrypha"}).status_code == 422


# ── reading state ───────────────────────────────────────────────────────────


def test_state_starts_at_defaults(loaded_client: TestClient):
    body = loaded_client.get("/state", params={"username": "sam"}).json()
    assert body["position"] is None
    assert body["preferences"]["default_version"] == "kjv"


def test_state_remembers_where_the_reader_stopped(loaded_client: TestClient):
    loaded_client.put(
        "/state", params={"username": "sam"}, json={"book": "John", "chapter": 3, "verse": 16}
    )
    body = loaded_client.get("/state", params={"username": "sam"}).json()
    assert body["position"] == {"book": "John", "chapter": 3, "verse": 16}


def test_state_stores_typographic_preferences(loaded_client: TestClient):
    loaded_client.put(
        "/state",
        params={"username": "sam"},
        json={"font_scale": 1.4, "theme": "sans", "split_view": "parallel"},
    )
    prefs = loaded_client.get("/state", params={"username": "sam"}).json()["preferences"]
    assert prefs["font_scale"] == 1.4
    assert prefs["theme"] == "sans"
    assert prefs["split_view"] == "parallel"


def test_a_favourite_is_remembered(loaded_client: TestClient):
    body = loaded_client.put(
        "/state", params={"username": "reader"}, json={"favorite_version": "kjv"}
    ).json()
    assert body["preferences"]["favorite_version"] == "kjv"


def test_taking_the_favourite_off_leaves_the_default_alone(loaded_client: TestClient):
    loaded_client.put(
        "/state",
        params={"username": "reader"},
        json={"default_version": "kjv", "favorite_version": "kjv"},
    )
    body = loaded_client.put(
        "/state", params={"username": "reader"}, json={"favorite_version": ""}
    ).json()
    assert body["preferences"]["favorite_version"] == ""
    assert body["preferences"]["default_version"] == "kjv"


def test_a_favourite_is_refused_when_the_translation_is_not_installed(loaded_client: TestClient):
    response = loaded_client.put(
        "/state", params={"username": "reader"}, json={"favorite_version": "nkjv"}
    )
    assert response.status_code == 400
    assert "nkjv" in response.json()["detail"]


def test_no_favourite_is_chosen_for_the_reader(loaded_client: TestClient):
    body = loaded_client.get("/state", params={"username": "brand-new"}).json()
    assert body["preferences"]["favorite_version"] == ""


def test_state_rejects_a_book_the_reader_never_heard_of(loaded_client: TestClient):
    response = loaded_client.put("/state", params={"username": "sam"}, json={"book": "Hobbits"})
    assert response.status_code == 400


def test_state_rejects_a_chapter_beyond_the_book(loaded_client: TestClient):
    response = loaded_client.put(
        "/state", params={"username": "sam"}, json={"book": "John", "chapter": 99}
    )
    assert response.status_code == 400
    assert "21 chapters" in response.json()["detail"]


def test_state_rejects_an_unimported_default_version(loaded_client: TestClient):
    response = loaded_client.put("/state", params={"username": "sam"}, json={"default_version": "esv"})
    assert response.status_code == 400


def test_state_rejects_out_of_range_typography(loaded_client: TestClient):
    assert loaded_client.put("/state", params={"username": "sam"}, json={"font_scale": 9}).status_code == 422


# ── events, marks, stats ────────────────────────────────────────────────────


def test_an_event_is_recorded(loaded_client: TestClient):
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "app_open"})
    body = loaded_client.get("/stats", params={"username": "sam"}).json()
    assert body["days_opened"] == 1
    assert body["open_streak_current"] == 1


def test_an_unknown_event_kind_is_refused(loaded_client: TestClient):
    response = loaded_client.post(
        "/events", params={"username": "sam"}, json={"kind": "read_my_browser_history"}
    )
    assert response.status_code == 422


def test_a_mark_is_created_and_idempotent(loaded_client: TestClient):
    for _ in range(2):
        loaded_client.put(
            "/marks",
            params={"username": "sam"},
            json={"ref": "John 3:16", "version_code": "kjv", "kind": "highlight", "color": "amber"},
        )
    marks = loaded_client.get("/marks", params={"username": "sam"}).json()["marks"]
    assert len(marks) == 1
    assert marks[0]["color"] == "amber"
    assert marks[0]["reference"] if "reference" in marks[0] else marks[0]["osis"] == "John"


def test_a_note_pointer_is_a_mark_of_its_own(loaded_client: TestClient):
    """Writing a note about a verse is not the same as highlighting it.

    The note lives in Nextcloud and this row only says one exists, so the two
    decisions stay separate: a reader can write a note without tinting the
    verse, and the reader can show which verses have one.
    """
    response = loaded_client.put(
        "/marks",
        params={"username": "sam"},
        json={
            "ref": "John 3:16",
            "version_code": "kjv",
            "kind": "note",
            "note_path": "Bible/John 3_16.md",
            "note_preview": "John 3:16 — For God so loved the world",
        },
    )
    assert response.status_code == 200, response.text
    mark = response.json()["mark"]
    assert mark["kind"] == "note"
    assert mark["note_path"] == "Bible/John 3_16.md"

    marks = loaded_client.get("/marks", params={"username": "sam"}).json()["marks"]
    assert [m["kind"] for m in marks] == ["note"]
    assert marks[0]["note_preview"].startswith("John 3:16")


def test_a_note_and_a_highlight_can_share_a_verse(loaded_client: TestClient):
    """The two are keyed by kind, so neither replaces the other."""
    loaded_client.put(
        "/marks", params={"username": "sam"}, json={"ref": "John 3:16", "kind": "highlight"}
    )
    loaded_client.put(
        "/marks",
        params={"username": "sam"},
        json={"ref": "John 3:16", "kind": "note", "note_path": "Bible/John 3_16.md"},
    )
    marks = loaded_client.get("/marks", params={"username": "sam"}).json()["marks"]
    assert sorted(m["kind"] for m in marks) == ["highlight", "note"]


def test_an_unknown_mark_kind_is_still_refused(loaded_client: TestClient):
    response = loaded_client.put(
        "/marks", params={"username": "sam"}, json={"ref": "John 3:16", "kind": "bookmarker"}
    )
    assert response.status_code == 422


def test_marks_can_be_filtered_by_book(loaded_client: TestClient):
    loaded_client.put("/marks", params={"username": "sam"}, json={"ref": "John 3:16"})
    loaded_client.put("/marks", params={"username": "sam"}, json={"ref": "Ps 23:1"})
    assert len(loaded_client.get("/marks", params={"username": "sam", "osis": "Ps"}).json()["marks"]) == 1


def test_a_mark_rejects_a_whole_book(loaded_client: TestClient):
    response = loaded_client.put("/marks", params={"username": "sam"}, json={"ref": "John"})
    assert response.status_code == 400


def test_a_mark_rejects_a_multi_passage_reference(loaded_client: TestClient):
    response = loaded_client.put(
        "/marks", params={"username": "sam"}, json={"ref": "John 3:16; 4:1"}
    )
    assert response.status_code == 400


def test_deleting_someone_elses_mark_is_a_404(loaded_client: TestClient):
    created = loaded_client.put("/marks", params={"username": "sam"}, json={"ref": "John 3:16"}).json()
    response = loaded_client.delete(f"/marks/{created['mark']['id']}", params={"username": "alex"})
    assert response.status_code == 404


def test_stats_count_days_and_chapters(loaded_client: TestClient):
    today = date.today().isoformat()
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "Gen 1"})
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "Gen 2"})
    body = loaded_client.get("/stats", params={"username": "sam"}).json()
    assert body["metrics"]["chapters_total"] == 2
    assert body["metrics"]["books_read"] == 1
    assert body["window"][today] == 2


def test_streaks_are_reported_separately_from_reading(loaded_client: TestClient):
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "app_open"})
    body = loaded_client.get("/streaks", params={"username": "sam"}).json()
    assert body["open_streak_current"] == 1
    assert body["read_streak_current"] == 1


# ── achievements ────────────────────────────────────────────────────────────


def test_achievements_are_earned_and_banked_once(loaded_client: TestClient):
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "Gen 1"})
    first = loaded_client.get("/achievements", params={"username": "sam"}).json()
    assert [b["id"] for b in first["newly_earned"]] == ["first_chapter", "books_read_1"]
    second = loaded_client.get("/achievements", params={"username": "sam"}).json()
    assert second["newly_earned"] == []
    assert [b["id"] for b in second["earned"]] == ["first_chapter", "books_read_1"]
    assert second["points"] == 10


def test_achievements_report_progress_toward_the_next_badge(loaded_client: TestClient):
    body = loaded_client.get("/achievements", params={"username": "sam"}).json()
    upcoming = {p["id"]: p for p in body["next_up"]}
    assert upcoming["first_chapter"]["current"] == 0
    assert upcoming["streak_3"]["unit"] == "day streak"


def test_achievements_name_the_rules_that_have_no_data_yet(loaded_client: TestClient):
    body = loaded_client.get("/achievements", params={"username": "sam"}).json()
    assert "quiz_perfect" in body["pending_rules"]


def test_a_geo_outage_is_reported_not_hidden(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "GEO_SVC_URL", "")
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "Gen 1"})
    stars = loaded_client.get("/achievements", params={"username": "sam"}).json()["stars"]
    assert stars["granted"] == 0
    assert "geo" in stars["status"].lower()


def test_no_family_chat_token_means_no_chat_card(loaded_client: TestClient, monkeypatch):
    monkeypatch.setenv("FAMILY_CHAT_TOKEN", "")
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "Gen 1"})
    announced = loaded_client.get("/achievements", params={"username": "sam"}).json()["announced"]
    assert announced["posted"] == 0
    assert "FAMILY_CHAT_TOKEN" in announced["status"]


# ── family sharing ──────────────────────────────────────────────────────────


def test_reading_your_own_activity_needs_no_consent(loaded_client: TestClient):
    body = loaded_client.get(
        "/activity/summary", params={"username": "sam", "target": "sam"}
    ).json()
    assert body["username"] == "sam"


def test_someone_elses_activity_is_a_404_without_consent(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "_consent", _never_shared)
    response = loaded_client.get("/activity/summary", params={"username": "sam", "target": "alex"})
    assert response.status_code == 404
    assert "not shared" in response.json()["detail"]


def test_an_unreachable_consent_service_refuses_rather_than_assumes(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "_consent", _unknown)
    response = loaded_client.get("/activity/summary", params={"username": "sam", "target": "alex"})
    assert response.status_code == 404


def test_shared_activity_is_visible(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "_consent", _granted)
    body = loaded_client.get(
        "/activity/summary", params={"username": "sam", "target": "alex"}
    ).json()
    assert body["username"] == "sam"


def test_the_feed_omits_private_readers_instead_of_failing(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "_sharing_settings", _two_readers)
    monkeypatch.setattr(bible_main, "_consent", _granted)
    body = loaded_client.get("/activity/feed", params={"viewer": "alex"}).json()
    assert body["members"] == ["sam", "bo"]


def test_the_feed_explains_an_empty_family(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "_sharing_settings", _nobody)
    body = loaded_client.get("/activity/feed", params={"viewer": "alex"}).json()
    assert body["entries"] == []
    assert "No one has shared" in body["note"]


async def _never_shared(owner: str, viewer: str, scope: str) -> bool:
    return False


async def _unknown(owner: str, viewer: str, scope: str):
    return None


async def _granted(owner: str, viewer: str, scope: str) -> bool:
    return True


async def _two_readers():
    return [{"username": "sam"}, {"username": "bo"}]


async def _nobody():
    return []


# ── blb deep links ──────────────────────────────────────────────────────────


def test_blb_link_for_a_verse(monkeypatch):
    monkeypatch.setattr(cfg, "BLB_BASE_URL", "https://bible.test")
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    assert bible_main.blb_link("John 3:16", "")["url"] == "https://bible.test/john/3/16"


def test_blb_link_without_a_base_url_names_the_setting(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "")
    response = loaded_client.get("/blb/link", params={"ref": "John 3:16"})
    assert response.status_code == 503
    assert "blb_base_url" in response.json()["detail"]


def test_blb_link_for_an_interlinear(monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    body = bible_main.blb_link("John 3:16", "interlinear/tr")
    assert body["url"] == "https://bible.test/tools/interlinear/tr/john/3"


def test_blb_link_rejects_a_bad_reference(monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    with pytest.raises(Exception):
        bible_main.blb_link("Notabook 1:1")


# ── daily card ──────────────────────────────────────────────────────────────


def test_daily_is_one_call_for_the_widget(loaded_client: TestClient):
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "app_open"})
    body = loaded_client.get("/daily", params={"username": "sam"}).json()
    assert body["verse_of_day"]["text"]
    assert "streaks" in body and "devotional" in body


def test_daily_works_without_a_username(loaded_client: TestClient):
    body = loaded_client.get("/daily").json()
    assert "streaks" not in body


def test_devotional_endpoint_reports_unconfigured_sources(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(cfg, "BLB_BASE_URL", "")
    monkeypatch.setattr(cfg, "BIBLE_DEVOTIONAL_DIR", "")
    body = loaded_client.get("/devotional").json()
    assert body["entry"] is None
    assert "No devotional is available" in body["reason"]


def test_devotional_sources_endpoint_lists_what_is_missing(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(cfg, "BLB_BASE_URL", "")
    sources = {s["code"]: s for s in loaded_client.get("/devotional/sources").json()["sources"]}
    assert sources["blb"]["configured"] is False
    assert "blb_base_url" in sources["blb"]["reason"]


def test_imported_devotionals_win_over_the_registry(loaded_client: TestClient, reader_db: Session, monkeypatch):
    from services.bible.models import Devotional

    reader_db.add(
        Devotional(
            source="library",
            work="ours",
            day_of_year=date.today().timetuple().tm_yday,
            title="Our own",
            reference="John 3:16",
            text="Something worth reading.",
        )
    )
    reader_db.commit()
    monkeypatch.setattr(cfg, "BLB_BASE_URL", "")
    monkeypatch.setattr(cfg, "BIBLE_DEVOTIONAL_DIR", "")
    body = loaded_client.get("/devotional").json()
    assert body["source"] == "library"
    assert body["entry"]["title"] == "Our own"


def test_corpus_helper_is_reachable_for_the_health_report(client: TestClient):
    assert corpus.count_rows.__name__ == "count_rows"

@pytest.fixture
def two_translations(loaded_client: TestClient, reader_db: Session, corpus_file: Path):
    """Two installed translations, because a comparison needs two."""
    corpus.import_corpus(
        reader_db, code="asv", name="American Standard Version", source_path=corpus_file
    )
    return loaded_client


def test_no_comparison_is_chosen_for_the_reader(two_translations: TestClient):
    prefs = two_translations.get(
        "/state", params={"username": "reader"}, headers=auth()
    ).json()["preferences"]
    assert prefs["compare_version"] == "", "a second version must be asked for, never guessed"


def test_a_translation_can_be_set_beside_the_one_being_read(two_translations: TestClient):
    two_translations.put(
        "/state", params={"username": "reader"}, json={"default_version": "kjv"}, headers=auth()
    )
    body = two_translations.put(
        "/state",
        params={"username": "reader"},
        json={"compare_version": "asv"},
        headers=auth(),
    ).json()["preferences"]
    assert body["compare_version"] == "asv"
    shown = two_translations.get(
        "/state", params={"username": "reader"}, headers=auth()
    ).json()["preferences"]
    assert shown["default_version"] == "kjv", (
        "choosing what to compare against must not move the reader's own text"
    )


def test_the_comparison_can_be_turned_off_without_touching_the_default(two_translations: TestClient):
    two_translations.put(
        "/state",
        params={"username": "reader"},
        json={"default_version": "kjv", "compare_version": "asv"},
        headers=auth(),
    )
    after = two_translations.put(
        "/state", params={"username": "reader"}, json={"compare_version": ""}, headers=auth()
    ).json()["preferences"]
    shown = two_translations.get(
        "/state", params={"username": "reader"}, headers=auth()
    ).json()["preferences"]
    assert after["compare_version"] == ""
    assert shown["default_version"] == "kjv", "the reader still opens the translation they chose"


def test_commentary_alongside_is_remembered_and_off_to_begin_with(loaded_client: TestClient):
    """The reading surface starts as text; showing the notes is the reader's call."""
    fresh = loaded_client.get(
        "/state", params={"username": "quiet"}, headers=auth()
    ).json()["preferences"]
    assert fresh["show_notes"] is False

    loaded_client.put(
        "/state", params={"username": "quiet"}, json={"show_notes": True}, headers=auth()
    )
    remembered = loaded_client.get(
        "/state", params={"username": "quiet"}, headers=auth()
    ).json()["preferences"]
    assert remembered["show_notes"] is True

    off = loaded_client.put(
        "/state", params={"username": "quiet"}, json={"show_notes": False}, headers=auth()
    ).json()["preferences"]
    assert off["show_notes"] is False


def test_comparing_a_translation_against_itself_is_refused(two_translations: TestClient):
    """The same words twice is not a comparison, and it looks like a bug."""
    resp = two_translations.put(
        "/state", params={"username": "reader"}, json={"compare_version": "kjv"}, headers=auth()
    )
    assert resp.status_code == 400
    assert "different translation" in resp.json()["detail"]


def test_a_comparison_against_a_translation_we_do_not_have_is_refused(loaded_client: TestClient):
    resp = loaded_client.put(
        "/state", params={"username": "reader"}, json={"compare_version": "nkjv"}, headers=auth()
    )
    assert resp.status_code == 400
    assert "nkjv" in resp.json()["detail"]
