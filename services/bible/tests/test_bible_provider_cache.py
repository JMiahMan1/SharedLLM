"""Tests for the long-term provider cache.

The cache exists because api.bible's free plan allows about 5,000 requests a
month and a whole Bible costs about 1,189, so a fetch that cannot resume is a
month's allowance. These tests pin the properties that make it safe: a cached
chapter is served without a request, a cache entry cannot be half-written, a
differently-shaped dialect gets its own space rather than silently reusing old
entries, and an import that would need more requests than the budget allows is
refused before anything is fetched.
"""

import json
from pathlib import Path

import pytest

from services.bible import importer, provider_cache
from services.bible.provider_cache import CACHE_VERSION, ChapterCache, call_budget, cache_root


def make_cache(tmp_path: Path, *, provider: str = "api.bible", base: str = "https://host", translation: str = "T1") -> ChapterCache:
    return ChapterCache(
        tmp_path, provider_code=provider, base_url=base, translation_id=translation
    )


def test_a_cache_that_is_asked_for_nothing_explains_itself_instead_of_failing_silently(tmp_path):
    cache = ChapterCache(
        None, provider_code="api.bible", base_url="https://host", translation_id="T1"
    )
    assert cache.enabled is False
    assert "BIBLE_PROVIDER_CACHE" in cache.reason
    assert cache.get_books() is None
    assert cache.get_chapter("GEN.1") is None


def test_a_chapter_is_served_from_disk_without_asking_the_provider(tmp_path):
    cache = make_cache(tmp_path)
    cache.put_chapter("GEN.1", {"data": {"content": [{"name": "verse"}]}})
    assert cache.get_chapter("GEN.1") == {"data": {"content": [{"name": "verse"}]}}
    assert cache.stats.calls == 0, "reading the cache must never look like a request"


def test_a_different_chapter_is_not_served_from_the_first_chapter(tmp_path):
    cache = make_cache(tmp_path)
    cache.put_chapter("GEN.1", {"data": {"id": "GEN.1"}})
    assert cache.get_chapter("GEN.2") is None


def test_a_corrupt_entry_is_a_miss_and_is_never_a_wrong_verse(tmp_path):
    cache = make_cache(tmp_path)
    path = cache.chapter_path("GEN.1")
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("this is not json", encoding="utf-8")
    assert cache.get_chapter("GEN.1") is None

    path.write_text(json.dumps({"chapter": "GEN.1", "payload": {}}), encoding="utf-8")
    assert cache.get_chapter("GEN.1") is None, "an empty payload is not a chapter"

    path.write_text(json.dumps({"chapter": "GEN.9", "payload": {"a": 1}}), encoding="utf-8")
    assert cache.get_chapter("GEN.1") is None, "an entry filed under the wrong chapter is a miss"


def test_a_killed_process_cannot_leave_a_half_written_chapter(tmp_path):
    """The write is atomic, so a chapter is either complete or absent."""
    cache = make_cache(tmp_path)
    cache.put_chapter("GEN.1", {"data": {"content": "x"}})
    path = cache.chapter_path("GEN.1")
    assert json.loads(path.read_text(encoding="utf-8"))["payload"]["data"]["content"] == "x"
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]


def test_an_empty_payload_is_refused_rather_than_cached_as_truth(tmp_path):
    cache = make_cache(tmp_path)
    for empty in (None, "", [], {}):
        with pytest.raises(ValueError):
            cache.put_chapter("GEN.1", empty)


def test_a_changed_dialect_gets_its_own_space_instead_of_reusing_old_text(tmp_path, monkeypatch):
    """A parser fix must not serve bytes parsed under the old rules."""
    cache = make_cache(tmp_path)
    cache.put_chapter("GEN.1", {"old": True})
    monkeypatch.setattr(provider_cache, "CACHE_VERSION", f"{CACHE_VERSION}-fixed")
    after = make_cache(tmp_path)
    assert after.translation_dir != cache.translation_dir
    assert after.get_chapter("GEN.1") is None


def test_a_different_host_gets_its_own_space(tmp_path):
    first = make_cache(tmp_path, base="https://api.scripture.api.bible")
    moved = make_cache(tmp_path, base="https://somewhere-else.example")
    assert first.translation_dir != moved.translation_dir


def test_the_api_key_is_not_part_of_the_cache_identity(tmp_path):
    """Rotating a key must not throw away months of fetched text."""
    first = ChapterCache(
        tmp_path, provider_code="api.bible", base_url="https://host", translation_id="T1", key_fingerprint="old"
    )
    rotated = ChapterCache(
        tmp_path, provider_code="api.bible", base_url="https://host", translation_id="T1", key_fingerprint="new"
    )
    assert first.translation_dir == rotated.translation_dir


def test_two_translations_do_not_share_a_directory(tmp_path):
    one = make_cache(tmp_path, translation="T1")
    two = make_cache(tmp_path, translation="T2")
    assert one.translation_dir != two.translation_dir


def test_the_book_list_is_cached_so_a_whole_translation_can_cost_nothing(tmp_path):
    cache = make_cache(tmp_path)
    cache.put_books([{"id": "GEN"}])
    assert cache.get_books() == [{"id": "GEN"}]


def test_the_translation_name_is_cached_too(tmp_path):
    cache = make_cache(tmp_path)
    cache.put_name("New International Version 2011")
    assert cache.get_name() == "New International Version 2011"


def test_missing_chapters_is_the_list_a_fetch_would_still_request(tmp_path):
    cache = make_cache(tmp_path)
    for chapter in ("GEN.1", "GEN.2", "GEN.3"):
        cache.put_chapter(chapter, {"data": {}})
    assert cache.missing_chapters(["GEN.1", "GEN.2", "GEN.3", "GEN.4"]) == ["GEN.4"]


def test_a_fully_cached_translation_needs_no_requests_at_all(tmp_path):
    cache = make_cache(tmp_path)
    for number in range(1, 5):
        cache.put_chapter(f"GEN.{number}", {"data": {}})
    assert cache.missing_chapters([f"GEN.{n}" for n in range(1, 5)]) == []


def test_the_budget_is_read_from_configuration(monkeypatch):
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CALL_BUDGET", "1200", raising=False)
    assert call_budget() == 1200
    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CALL_BUDGET", "", raising=False)
    assert call_budget() is None


@pytest.mark.parametrize("bad", ["12oo", "lots", "12.5", "0", "-5"])
def test_a_budget_that_is_set_but_unreadable_is_an_error_not_no_limit(monkeypatch, bad):
    """A typo must not quietly remove the guardrail.

    This number is the only thing between a mistyped setting and a wasted
    provider allowance, so a value that cannot be read has to be refused loudly
    instead of being read as "no ceiling".
    """
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CALL_BUDGET", bad, raising=False)
    with pytest.raises(ValueError) as excinfo:
        call_budget()
    assert "BIBLE_PROVIDER_CALL_BUDGET" in str(excinfo.value)
    assert bad in str(excinfo.value)


def test_the_cache_root_is_derived_from_the_database_when_not_configured(monkeypatch):
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CACHE", "", raising=False)
    monkeypatch.setattr(cfg, "BIBLE_DATABASE_URL", "sqlite:////data/bible.db", raising=False)
    assert cache_root() == Path("/data/provider-cache")


def test_an_explicit_cache_directory_wins_over_the_derived_one(monkeypatch):
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CACHE", "/srv/bible-cache", raising=False)
    monkeypatch.setattr(cfg, "BIBLE_DATABASE_URL", "sqlite:////data/bible.db", raising=False)
    assert cache_root() == Path("/srv/bible-cache")


def test_describe_says_where_the_cache_is_and_that_it_is_kept(tmp_path):
    described = make_cache(tmp_path).describe()
    assert "api.bible" in described
    assert "not" in described.split("kept")[-1] or "indefinitely" in described


class _Book:
    chapters = 3


class _Books:
    BOOK_BY_OSIS = {"Gen": _Book()}


def test_a_fake_provider_reports_the_cache_and_the_call_accounting(tmp_path):
    """The import log has to make the cost legible, or it gets repeated."""
    cache = make_cache(tmp_path)
    cache.put_chapter("GEN.1", {"data": {"content": "x"}})
    lines = importer._cache_log(cache)
    assert "Requests used: 0" in lines[0]
    assert "Nothing was requested" in lines[1]

    class Used:
        calls = 1189
        books_hits = 0
        chapter_hits = 5

    cache.stats = Used()
    assert "Requests used: 1189" in importer._cache_log(cache)[0]


def test_the_call_log_shows_what_is_still_outstanding(tmp_path):
    cache = make_cache(tmp_path)
    estimate = {"chapters": 1189, "remaining": 680}
    lines = importer._call_log(estimate, cache, 1200)
    assert "680 of 1189" in lines[0]
    assert "680 requests" in lines[0]
    assert "would be refused" not in lines[1]

    over = dict(estimate, remaining=1300)
    assert "would be refused" in importer._call_log(over, cache, 1200)[1]


def test_a_fully_cached_translation_is_described_as_costing_nothing(tmp_path):
    cache = make_cache(tmp_path)
    lines = importer._call_log({"chapters": 1189, "remaining": 0}, cache, 1200)
    assert "no requests at all" in lines[0]


def test_an_unreadable_budget_refuses_the_import_instead_of_running_it(monkeypatch, tmp_path):
    """A typo in the budget stops the run; it does not become 'no ceiling'.

    The refusal has to reach the operator as a recorded failed run carrying the
    reason, because that is what the Admin panel reads back afterwards.
    """
    import services.config as cfg
    from services.bible.models import BibleVersion, UserBibleState
    from sqlmodel import Session, SQLModel, create_engine, select

    monkeypatch.setattr(cfg, "BIBLE_PROVIDER_CALL_BUDGET", "12oo", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    SQLModel.metadata.create_all(engine)

    class FakeProvider:
        code = "api.bible"
        title = "api.bible"
        base_url = "https://api.scripture.api.bible"

        def configured(self):
            return True

        def unconfigured_reason(self):
            return ""

        async def estimate(self, translation_id, *, cache=None):
            return {
                "translation_id": translation_id, "name": "NLT", "books": 66,
                "chapters": 1189, "cached": 509, "remaining": 680, "calls": 680,
            }

        async def fetch(self, *args, **kwargs):
            raise AssertionError("must not fetch anything when the budget is unreadable")

    class Registry:
        def get(self, code):
            return FakeProvider()

        def describe(self):
            return []

        def codes(self):
            return ["api.bible"]

        def configured(self):
            return True

    plan = importer.ImportPlan(code="nlt", kind="json", provider="api.bible", provider_id="nlt")
    with Session(engine) as session:
        report = importer.run(session, plan, registry=Registry(), import_dir=str(tmp_path))

    assert report.status == "failed", report.message
    assert "BIBLE_PROVIDER_CALL_BUDGET" in report.message
    assert "12oo" in report.message
    assert "No request was made" in report.message
    assert report.verse_count == 0

    with Session(engine) as session:
        assert session.exec(select(BibleVersion)).all() == []
        assert session.exec(select(UserBibleState)).all() == []


def test_the_operator_can_manage_every_bible_setting_from_the_config_database():
    """`.env` is the seed; the settings a person changes belong in the DB.

    Anything wired into settings_map has to be seeded, or Admin > Settings will
    not render it and the only way to change it is to edit a file on the host.
    """
    import json as _json

    import inspect

    import services.config as cfg
    from services.bible import main
    from services.identity.models import DEFAULT_GLOBAL_SETTINGS

    # settings_map is a local inside resolve_runtime_config(), so it is asserted
    # against the source rather than imported.
    source = inspect.getsource(cfg.resolve_runtime_config)
    live_source = inspect.getsource(main._live_settings)
    seeded = {row["key"] for row in DEFAULT_GLOBAL_SETTINGS}

    # bible_api_key is deliberately NOT in settings_map: the admin panel reads it
    # live from the settings database on every call so a key pasted into the UI
    # works without restarting the service. Everything else is a boot-time seed.
    live = {"bible_api_key"}
    for key in (
        "bible_svc_url",
        "blb_base_url",
        "bible_devotional_dir",
        "bible_import_dir",
        "bible_provider_cache",
        "bible_provider_call_budget",
    ):
        assert key in seeded, f"{key} is not seeded, so Admin > Settings cannot show it"
        assert f'"{key}"' in source, f"{key} cannot be overridden from the config DB"

    assert "bible_api_key" in seeded, "the api key must be seeded to be editable"
    assert '"bible_api_key"' not in source, (
        "the api key is read live on purpose; putting it in settings_map would make "
        "it a boot-time value that needs a restart to change"
    )
    assert "bible_api_key" in inspect.getsource(main._provider_registry)
    assert "/api/global-settings" in live_source

    assert len(seeded) == len(DEFAULT_GLOBAL_SETTINGS), "duplicate GlobalSetting keys"
    for row in DEFAULT_GLOBAL_SETTINGS:
        _json.dumps(row), "every seeded setting must be JSON-clean"
