"""The admin import surface: NKJV as primary, online providers, importing more.

Three things are being held to here. The manifest marks which translation a
reader gets by default. A provider declared but unconfigured must say which
setting is missing rather than pretending not to exist. And every import --
succeeded or refused -- leaves a record, because "it said it worked" needs an
answer afterwards.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from services.bible import corpus, importer, providers
from services.bible.books import BOOKS
from services.bible import main as bible_main
from services.bible.corpus import CorpusError
from services.bible.models import BibleVerse, ImportRun
from services.bible.providers import ProviderError, ProviderUnavailable


def _load_json(path: Path) -> list[dict]:
    """Read a staged corpus file the way the importer does."""
    return json.loads(path.read_text(encoding="utf-8"))

SECRET = "test-secret"


def _manifest(tmp_path: Path, **overrides) -> Path:
    base = {
        "schemaVersion": 1,
        "kind": "jarvis.bible.corpus",
        "versions": [
            {
                "code": "nkjv",
                "name": "New King James Version",
                "language": "en",
                "license_class": "licensed",
                "rights_holder": "Thomas Nelson",
                "primary": True,
            },
            {
                "code": "kjv",
                "name": "King James Version",
                "language": "en",
                "license_class": "public_domain",
                "rights_holder": "",
                "source_url": "https://example.test/kjv.json",
            },
        ],
        "editions": [],
    }
    base.update(overrides)
    path = tmp_path / "corpus_manifest.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


# ── NKJV is the primary translation ──────────────────────────────────────────


def test_the_manifest_marks_one_primary_translation():
    assert corpus.primary_code() == "nkjv"


def test_the_default_is_the_primary_translation_when_its_text_is_installed(session, corpus_file):
    corpus.import_corpus(session, code="kjv", name="King James Version", source_path=corpus_file)
    corpus.import_corpus(session, code="nkjv", name="New King James Version", source_path=corpus_file)
    assert corpus.default_version_code(session) == "nkjv"


def test_the_default_falls_back_to_the_richest_installed_translation(session, corpus_file, tmp_path):
    """NKJV uninstalled must not send the reader to a hardcoded other code."""
    corpus.import_corpus(session, code="kjv", name="King James Version", source_path=corpus_file)
    manifest = _manifest(tmp_path)
    assert corpus.default_version_code(session, manifest_path=manifest) == "kjv"


def test_no_installed_translation_yields_no_default_rather_than_a_guess(session):
    assert corpus.default_version_code(session) == ""


def test_the_catalogue_says_which_one_is_primary_and_where_the_others_come_from(session):
    entries = {entry["code"]: entry for entry in corpus.catalogue(session)}
    assert entries["nkjv"]["primary"] is True
    assert entries["kjv"]["primary"] is False
    assert entries["esv"]["provider"] == "api.bible"


def test_a_reader_with_no_preference_is_handed_the_primary_translation(reader_db, corpus_file):
    corpus.import_corpus(reader_db, code="kjv", name="King James Version", source_path=corpus_file)
    corpus.import_corpus(reader_db, code="nkjv", name="New King James Version", source_path=corpus_file)


def test_the_daily_card_uses_the_primary_translation(loaded_client: TestClient, reader_db: Session, corpus_file):
    corpus.import_corpus(reader_db, code="nkjv", name="New King James Version", source_path=corpus_file)
    body = loaded_client.get("/daily").json()
    assert body["verse_of_day"]["version"] == "nkjv"


def test_two_primary_translations_are_a_manifest_error(tmp_path):
    path = tmp_path / "corpus_manifest.json"
    payload = json.loads(_manifest(tmp_path).read_text())
    payload["versions"][1]["primary"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError) as exc:
        corpus.load_manifest(path)
    assert "more than one translation primary" in str(exc.value)


# ── providers ────────────────────────────────────────────────────────────────


def test_the_manifest_declares_an_online_provider():
    entries = providers.load_provider_config()
    api_bible = next(e for e in entries if e["code"] == "api.bible")
    assert api_bible["base_url"].startswith("https://")
    assert api_bible["requires"] == "bible_api_key"


def test_a_manifest_with_no_providers_block_is_supported(tmp_path):
    assert providers.load_provider_config(_manifest(tmp_path)) == []


def test_a_providers_block_that_is_not_a_list_is_refused(tmp_path):
    path = tmp_path / "corpus_manifest.json"
    path.write_text(json.dumps({"kind": "jarvis.bible.corpus", "versions": [], "providers": {}}), encoding="utf-8")
    with pytest.raises(CorpusError) as exc:
        providers.load_provider_config(path)
    assert "not a list" in str(exc.value)


def test_a_provider_declared_twice_is_refused(tmp_path):
    entry = {"code": "api.bible", "title": "a", "base_url": "https://a.test"}
    path = tmp_path / "corpus_manifest.json"
    path.write_text(
        json.dumps({"kind": "jarvis.bible.corpus", "versions": [], "providers": [entry, dict(entry, title="b")]}),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError) as exc:
        providers.load_provider_config(path)
    assert "twice" in str(exc.value)


def test_a_provider_with_no_key_says_which_setting_to_set():
    registry = providers.build_registry(
        providers.load_provider_config(), settings={"bible_api_key": ""}
    )
    described = registry.describe()[0]
    assert described["configured"] is False
    assert "bible_api_key" in described["reason"]
    assert registry.configured() == []


def test_a_provider_with_a_key_is_configured():
    registry = providers.build_registry(providers.load_provider_config(), settings={"bible_api_key": "k"})
    assert registry.describe()[0]["configured"] is True
    assert len(registry.configured()) == 1


def test_a_provider_with_no_base_url_is_refused_rather_than_guessed(tmp_path):
    entry = {"code": "api.bible", "title": "api.bible", "base_url": "", "requires": ""}
    registry = providers.build_registry([entry], settings={})
    assert registry.get("api.bible").configured() is False
    assert "no base_url" in registry.get("api.bible").unconfigured_reason()


def test_asking_for_an_unknown_provider_names_the_ones_we_offer():
    registry = providers.build_registry(providers.load_provider_config(), settings={})
    with pytest.raises(ProviderError) as exc:
        registry.get("somewhere-else")
    assert "api.bible" in str(exc.value)


def test_a_declared_provider_with_no_implementation_is_refused():
    with pytest.raises(ProviderError) as exc:
        providers.build_registry([{"code": "imaginary", "title": "x", "base_url": "https://x.test"}], settings={})
    assert "no implementation" in str(exc.value)


@pytest.mark.parametrize(
    ("token", "expected"),
    [("GEN", "Gen"), ("gen", "Gen"), ("43JHN", "John"), ("66REV", "Rev"), ("Ps", "Ps"), ("430", "")],
)
def test_provider_book_tokens_map_onto_canonical_ids(token, expected):
    assert providers._osis(token) == expected


@pytest.mark.parametrize(
    ("token", "name", "expected"),
    [
        ("JUD", "Judges", "Judg"),
        ("JUD", "Jude", "Jude"),
        ("JHN", "John", "John"),
        ("PRO", "Proverbs", "Prov"),
        ("GEN", "Genesis", "Gen"),
        ("JUD", "Nonsense", "Jude"),
    ],
)
def test_a_colliding_abbreviation_loses_to_the_longer_book_name(token, name, expected):
    """``JUD`` is a listed abbreviation of Jude, so first-wins would lose Judges."""
    assert providers._osis(token, name) == expected


# ── api.bible fetch ──────────────────────────────────────────────────────────


def _chapter_payload(book_id: str, number: int, verses: int = 2) -> dict:
    """A chapter in the shape api.bible returns with ``content-type=text``.

    The whole chapter arrives as one string with ``[n]`` before each verse, which
    is the form the provider asks for because it cannot lose a verse.
    """
    reference = f"{book_id}.{number}"
    body = "Registration of Israel\u2019s Troops\n"
    for verse in range(1, verses + 1):
        body += f"  [{verse}] text for {reference}.{verse}\n"
    return {"verseCount": verses, "content": body}


class _FakeApiBible(providers.ApiBibleProvider):
    """A provider whose HTTP is replaced by a recorded book/chapter map."""

    def __init__(self, books: dict, *, missing_book: str = "", remote_name: str = "ESV"):
        super().__init__(
            code="api.bible",
            title="api.bible",
            base_url="https://api.bible",
            requires="bible_api_key",
            api_key="k",
        )
        self.books = books
        self.missing_book = missing_book
        self.remote_name = remote_name
        self.requested: list[str] = []

    def _book_id(self, osis: str) -> str:
        return self.books.get(osis.lower(), {}).get("id", osis.upper()[:3])

    async def _get(self, session, path: str, *, timeout=None):
        self.requested.append(path)
        if path.endswith("/books"):
            rows = [
                {"id": entry["id"], "abbreviation": code[:2], "name": code, "nameLong": entry["name"]}
                for code, entry in self.books.items()
                if code != self.missing_book
            ]
            return rows
        if "/chapters/" not in path:
            return {"data": {"id": path.rsplit("/", 1)[-1], "name": self.remote_name}}
        chapter = path.split("?")[0].rsplit("/", 1)[-1]
        book_id, _, number = chapter.partition(".")
        if "?" not in path:
            raise AssertionError(f"content-type was not requested for {chapter}")
        return _chapter_payload(book_id, int(number))


def _every_book_one_chapter(monkeypatch) -> dict:
    """A book map with one chapter each, so the fetch is 66 quick requests."""
    from services.bible.books import BOOKS

    monkeypatch.setitem(BOOKS[0], "chapters", 1)
    return {entry["osis"].lower(): {"id": entry["osis"].upper()[:3], "name": entry["name"]} for entry in BOOKS}


def test_a_fetched_bible_is_written_as_corpus_shaped_json(tmp_path, monkeypatch):
    books = _every_book_one_chapter(monkeypatch)
    provider = _FakeApiBible(books)
    path, name = importer._run_async(provider.fetch("de4e", tmp_path / "esv.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert name == "ESV", "a reader must never be shown the raw translation id"
    assert len(payload) == 66
    assert payload[0]["name"] == "Genesis"
    assert payload[0]["chapters"] == [["text for GEN.1.1", "text for GEN.1.2"]]
    assert all(len(row["chapters"]) >= 1 for row in payload)


def test_a_chapter_whose_markers_jump_is_refused_rather_than_written(tmp_path, monkeypatch):
    """A mangled response would misalign every verse number after the break."""
    books = _every_book_one_chapter(monkeypatch)
    provider = _FakeApiBible(books)

    async def jumped(self, session, path, *, timeout=None):
        self.requested.append(path)
        if path.endswith("/books"):
            return [{"id": entry["id"], "name": entry["name"]} for entry in self.books.values()]
        if "/chapters/" not in path:
            return {"data": {"id": path.rsplit("/", 1)[-1], "name": self.remote_name}}
        return {"verseCount": 5, "content": "  [1] first\n  [3] third\n"}

    provider._get = jumped.__get__(provider)
    destination = tmp_path / "esv.json"
    with pytest.raises(ProviderUnavailable, match="jumped to verse 3 after 1"):
        importer._run_async(provider.fetch("de4e", destination))
    assert not destination.exists()


def test_a_declared_verse_count_that_disagrees_is_not_treated_as_corruption(tmp_path, monkeypatch):
    """Numbers 1 declares 42 and has 54 verses, so the field is not a verse count."""
    books = _every_book_one_chapter(monkeypatch)
    provider = _FakeApiBible(books)

    async def generous(self, session, path, *, timeout=None):
        self.requested.append(path)
        if path.endswith("/books"):
            return [{"id": entry["id"], "name": entry["name"]} for entry in self.books.values()]
        if "/chapters/" not in path:
            return {"data": {"id": path.rsplit("/", 1)[-1], "name": self.remote_name}}
        chapter = path.split("?")[0].rsplit("/", 1)[-1]
        payload = _chapter_payload(*chapter.split("."), verses=2)
        payload["verseCount"] = 99
        return payload

    provider._get = generous.__get__(provider)
    path, _name = importer._run_async(provider.fetch("de4e", tmp_path / "esv.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["chapters"] == [["text for GEN.1.1", "text for GEN.1.2"]]


def test_a_provider_that_serves_a_partial_bible_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    books = _every_book_one_chapter(monkeypatch)
    provider = _FakeApiBible(books, missing_book="exod")
    destination = tmp_path / "esv.json"
    with pytest.raises(ProviderUnavailable) as exc:
        importer._run_async(provider.fetch("de4e", destination))
    assert "Exodus" in str(exc.value)
    assert not destination.exists()


def test_fetching_without_an_id_is_a_400(tmp_path):
    provider = _FakeApiBible({})
    with pytest.raises(ProviderError) as exc:
        importer._run_async(provider.fetch("", tmp_path / "x.json"))
    assert "Choose a translation" in str(exc.value)


def test_fetching_without_a_key_says_which_setting_to_set(tmp_path):
    provider = providers.ApiBibleProvider(
        code="api.bible", title="api.bible", base_url="https://api.bible", requires="bible_api_key", api_key=""
    )
    with pytest.raises(ProviderUnavailable) as exc:
        importer._run_async(provider.fetch("de4e", tmp_path / "x.json"))
    assert "bible_api_key" in str(exc.value)


# ── importer ─────────────────────────────────────────────────────────────────


def test_importing_a_json_translation_succeeds_and_records_the_run(session, corpus_file):
    report = importer.run(session, importer.ImportPlan(code="kjv", kind="json", source_path=str(corpus_file)))
    assert report.ok is True
    assert report.verse_count > 0
    assert report.book_count == 66
    runs = importer.recent_runs(session)
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["code"] == "kjv"
    assert "Imported" in runs[0]["log"][0]


def test_a_code_outside_the_manifest_is_refused_and_recorded(session, corpus_file):
    report = importer.run(
        session, importer.ImportPlan(code="made-up", kind="json", source_path=str(corpus_file))
    )
    assert report.ok is False
    assert "corpus manifest" in report.message
    runs = importer.recent_runs(session)
    assert runs[0]["status"] == "failed"
    assert "Refused" in " ".join(runs[0]["log"])


def test_a_missing_file_says_so_rather_than_installing_nothing_silently(session, tmp_path):
    report = importer.run(
        session, importer.ImportPlan(code="kjv", kind="json", source_path=str(tmp_path / "nope.json"))
    )
    assert report.ok is False
    assert "does not exist" in report.message


def test_an_unsupported_extension_names_the_supported_ones(session, tmp_path):
    path = tmp_path / "bible.doc"
    path.write_text("not a bible", encoding="utf-8")
    with pytest.raises(importer.ImportRefusal) as exc:
        importer.detect_kind(path)
    assert ".epub" in str(exc.value)


def test_a_study_edition_can_be_named_for_a_file_install(session, corpus_file):
    report = importer.run(
        session,
        importer.ImportPlan(
            code="nkjv", kind="json", source_path=str(corpus_file), edition="nkjv-tmn", edition_name="NKJV Study Bible"
        ),
    )
    assert report.ok is True
    assert corpus.list_editions(session, "nkjv")[0]["code"] == "nkjv-tmn"


def test_recent_runs_are_newest_first(session, corpus_file):
    importer.run(session, importer.ImportPlan(code="kjv", kind="json", source_path=str(corpus_file)))
    importer.run(session, importer.ImportPlan(code="nope", kind="json", source_path=str(corpus_file)))
    runs = importer.recent_runs(session)
    assert [r["code"] for r in runs] == ["nope", "kjv"]


# ── the admin endpoints ──────────────────────────────────────────────────────


def test_the_admin_page_reports_the_catalogue_providers_and_history(loaded_client: TestClient):
    body = loaded_client.get("/admin/imports").json()
    assert body["default_version"] == "kjv"
    assert body["primary"] == "nkjv"
    assert {v["code"] for v in body["versions"]} >= {"kjv", "nkjv", "esv"}
    assert body["providers"][0]["code"] == "api.bible"
    assert body["providers"][0]["configured"] is False
    assert "bible_api_key" in body["providers"][0]["reason"]
    assert body["kinds"] == ["json", "pdf", "epub"]
    assert body["runs"] == []


def test_the_admin_page_says_where_it_has_no_import_directory(loaded_client: TestClient, monkeypatch):
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_IMPORT_DIR", "")
    body = loaded_client.get("/admin/imports").json()
    assert body["import_dir"] is None
    assert "BIBLE_IMPORT_DIR" in body["import_dir_error"]


def test_importing_from_a_path_returns_the_report(loaded_client: TestClient, corpus_file, tmp_path):
    response = loaded_client.post(
        "/admin/imports", json={"code": "nkjv", "kind": "json", "source_path": str(corpus_file)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["verse_count"] > 0
    assert loaded_client.get("/admin/imports").json()["runs"][0]["code"] == "nkjv"


def test_a_refused_import_comes_back_as_a_report_not_an_exception(loaded_client: TestClient, tmp_path):
    response = loaded_client.post(
        "/admin/imports", json={"code": "kjv", "kind": "json", "source_path": str(tmp_path / "missing.json")}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "failed"
    assert "does not exist" in body["message"]


def test_an_unknown_kind_is_a_400_listing_the_real_ones(loaded_client: TestClient, corpus_file):
    response = loaded_client.post(
        "/admin/imports", json={"code": "kjv", "kind": "docx", "source_path": str(corpus_file)}
    )
    assert response.status_code == 400
    assert "epub" in response.json()["detail"]


def test_an_unconfigured_import_directory_is_reported_on_the_post_too(loaded_client: TestClient, monkeypatch, corpus_file):
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_IMPORT_DIR", "")
    response = loaded_client.post(
        "/admin/imports", json={"code": "kjv", "kind": "json", "source_path": str(corpus_file)}
    )
    assert response.status_code == 422
    assert "BIBLE_IMPORT_DIR" in response.json()["message"]


def test_an_upload_is_stored_and_installed(loaded_client: TestClient, corpus_file, tmp_path, monkeypatch):
    import services.config as cfg

    import services.bible.main as main_module

    monkeypatch_target = tmp_path / "uploads"
    monkeypatch_target.mkdir()

    monkeypatch.setattr(main_module, "_import_dir", lambda: target)
    original = main_module._import_dir
    main_module._import_dir = lambda: monkeypatch_target
    try:
        response = loaded_client.post(
            "/admin/imports/upload",
            files={"file": ("mini.json", corpus_file.read_bytes(), "application/json")},
            data={"code": "nkjv"},
        )
    finally:
        main_module._import_dir = original
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert (monkeypatch_target / "nkjv.json").exists()
    assert "Uploaded mini.json" in body["log"][0]


def test_an_upload_of_the_wrong_kind_is_refused_before_anything_is_written(
    loaded_client: TestClient, tmp_path, monkeypatch
):
    import services.bible.main as main_module

    target = tmp_path / "uploads2"
    target.mkdir()
    monkeypatch.setattr(main_module, "_import_dir", lambda: target)
    original = main_module._import_dir
    main_module._import_dir = lambda: target
    try:
        response = loaded_client.post(
            "/admin/imports/upload",
            files={"file": ("study.docx", b"not a bible", "application/octet-stream")},
            data={"code": "nkjv"},
        )
    finally:
        main_module._import_dir = original
    assert response.status_code == 400
    assert ".epub" in response.json()["detail"]
    assert list(target.iterdir()) == []


def test_the_admin_routes_need_the_internal_secret(engine, monkeypatch):
    monkeypatch.setattr(bible_main.app.state, "engine", engine)
    with TestClient(bible_main.app) as bare:
        assert bare.get("/admin/imports").status_code == 403
        assert bare.post("/admin/imports", json={"code": "kjv"}).status_code == 403

# ── asking a provider what it can supply ────────────────────────────────────
def _async_registry(factory):
    """``_provider_registry`` is async because it reads the live key from Identity."""

    async def _registry():
        return factory()

    return _registry


class _ListingProvider(providers.ApiBibleProvider):
    """A provider whose only job here is to answer ``/v1/bibles``."""

    def __init__(self, rows, **kwargs):
        super().__init__(**kwargs)
        self.rows = rows

    async def translations(self):
        return list(self.rows)


def test_asking_a_provider_is_a_separate_call(loaded_client: TestClient, monkeypatch):
    """Listing the catalogue must never need the network."""
    asked = []

    class _Registry:
        def describe(self_inner):
            return []

        def get(self, code):
            asked.append(code)

            class _One:
                async def translations(self_inner):
                    return [providers.RemoteTranslation("ESV", "English Standard Version", "eng")]

                def describe(self_inner):
                    return {"code": code, "title": code, "base_url": "https://api.bible", "requires": "bible_api_key", "note": "", "configured": True, "reason": ""}

            return _One()

    monkeypatch.setattr(bible_main, "_provider_registry", _async_registry(_Registry))
    response = loaded_client.get("/admin/providers/api.bible/translations")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["translations"][0]["id"] == "ESV"
    assert asked == ["api.bible"]
    assert loaded_client.get("/admin/imports").status_code == 200


def test_an_unknown_provider_names_the_ones_we_do_have(loaded_client: TestClient, monkeypatch):
    class _Registry:
        def get(self, code):
            raise providers.ProviderError(
                f"There is no provider called {code!r}. Declared: api.bible"
            )

    monkeypatch.setattr(bible_main, "_provider_registry", _async_registry(_Registry))
    response = loaded_client.get("/admin/providers/biblegateway/translations")
    assert response.status_code == 400
    assert "api.bible" in response.json()["detail"]


def test_an_unconfigured_provider_is_a_503_naming_the_setting(loaded_client: TestClient, monkeypatch):
    provider = providers.ApiBibleProvider(
        code="api.bible",
        title="api.bible online translations",
        base_url="https://api.bible",
        requires="bible_api_key",
        note="",
        api_key="",
    )

    class _Registry:
        def describe(self_inner):
            return []

        def get(self, code):
            return provider

    monkeypatch.setattr(bible_main, "_provider_registry", _async_registry(_Registry))
    response = loaded_client.get("/admin/providers/api.bible/translations")
    assert response.status_code == 503
    assert "bible_api_key" in response.json()["detail"]


def test_a_provider_that_answers_nothing_is_a_503(loaded_client: TestClient, monkeypatch):
    provider = _ListingProvider(
        [],
        code="api.bible",
        title="api.bible",
        base_url="https://api.bible",
        requires="bible_api_key",
        note="",
        api_key="k",
    )

    class _Registry:
        def describe(self_inner):
            return []

        def get(self, code):
            return provider

    monkeypatch.setattr(bible_main, "_provider_registry", _async_registry(_Registry))
    assert loaded_client.get("/admin/providers/api.bible/translations").status_code == 200


def test_the_declared_provider_is_configured_once_its_key_exists():
    registry = providers.build_registry(
        providers.load_provider_config(), settings={"bible_api_key": "k"}
    )
    assert registry.get("api.bible").configured()
    assert registry.get("api.bible").unconfigured_reason() == ""


# ── the key is read live, so saving it in the app works without a restart ──────


class _SettingsResponse:
    """Just the aiohttp surface ``_live_settings`` uses."""

    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _SettingsSession:
    def __init__(self, response: _SettingsResponse):
        self._response = response
        self.urls: list[str] = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.urls.append(url)
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_identity(monkeypatch, response: _SettingsResponse) -> _SettingsSession:
    session = _SettingsSession(response)
    monkeypatch.setattr(bible_main.aiohttp, "ClientSession", lambda *a, **k: session)
    return session


async def _registry_with(response: _SettingsResponse, monkeypatch, env_key: str = ""):
    """Resolve the registry the way a route does, with Identity stubbed out."""
    _patch_identity(monkeypatch, response)
    monkeypatch.setattr("services.config.BIBLE_API_KEY", env_key)
    return await bible_main._provider_registry()


@pytest.mark.asyncio
async def test_a_key_saved_in_the_app_is_used_without_a_restart(monkeypatch):
    registry = await _registry_with(
        _SettingsResponse(200, [{"key": "bible_api_key", "value": "saved-in-the-app"}]), monkeypatch
    )
    provider = registry.get("api.bible")
    assert provider.configured() is True
    assert provider.api_key == "saved-in-the-app"


@pytest.mark.asyncio
async def test_a_blank_saved_key_does_not_erase_one_the_environment_supplied(monkeypatch):
    registry = await _registry_with(_SettingsResponse(200, [{"key": "bible_api_key", "value": ""}]), monkeypatch, "from-env")
    assert registry.get("api.bible").api_key == "from-env"


@pytest.mark.asyncio
async def test_an_unreachable_identity_falls_back_to_the_boot_time_key(monkeypatch):
    registry = await _registry_with(_SettingsResponse(200, OSError("identity is down")), monkeypatch, "from-env")
    assert registry.get("api.bible").api_key == "from-env"


@pytest.mark.asyncio
async def test_no_key_anywhere_leaves_the_provider_listed_but_unconfigured(monkeypatch):
    registry = await _registry_with(_SettingsResponse(200, []), monkeypatch)
    provider = registry.get("api.bible")
    assert provider.configured() is False
    assert "bible_api_key" in provider.unconfigured_reason()


def _gapped_payload() -> list[dict]:
    """A corpus-shaped source whose text omits one verse the way NIV2011 does."""
    payload: list[dict] = []
    for book in BOOKS:
        chapters: list[list[str]] = []
        for number in range(1, book["chapters"] + 1):
            verses = [f"text for {book['osis']}.{number}.{v}" for v in (1, 2, 3)]
            if book["osis"] == "Matt" and number == 17:
                verses = [
                    text if not text.endswith(".2") else "" for text in verses
                ]
            chapters.append(verses)
        payload.append({"abbrev": book["osis"], "name": book["name"], "chapters": chapters})
    return payload


def test_a_blank_verse_is_still_a_refusal_without_allow_gaps(tmp_path):
    source = tmp_path / "gapped.json"
    source.write_text(json.dumps(_gapped_payload()), encoding="utf-8")
    with pytest.raises(CorpusError, match="Matthew 17:2 is blank in the source"):
        corpus.parse_source(_load_json(source))


def test_allow_gaps_keeps_the_slot_so_the_numbering_still_lines_up(tmp_path):
    source = tmp_path / "gapped.json"
    source.write_text(json.dumps(_gapped_payload()), encoding="utf-8")
    _, normalised = corpus.parse_source(_load_json(source), allow_gaps=True)
    matthew = dict(normalised)["Matt"]
    assert matthew[16] == ["text for Matt.17.1", "", "text for Matt.17.3"]
    assert corpus.omitted_verses(normalised) == ["Matthew 17:2"]


def test_a_gapped_source_imports_and_the_omission_is_reported(tmp_path, loaded):
    source = tmp_path / "gapped.json"
    source.write_text(json.dumps(_gapped_payload()), encoding="utf-8")
    summary = corpus.import_corpus(
        loaded,
        code="niv",
        name="New International Version",
        source_path=source,
        license_class="licensed",
        rights_holder="Biblica",
        allow_gaps=True,
    )
    assert summary["omitted"] == ["Matthew 17:2"]
    complete = sum(b["chapters"] for b in BOOKS) * 3
    assert summary["verses"] == complete - 1
    verses = loaded.exec(
        select(BibleVerse).where(BibleVerse.version_code == "niv").where(BibleVerse.osis == "Matt").where(BibleVerse.chapter == 17)
    ).all()
    numbers = [v.verse for v in verses]
    assert numbers == [1, 3]
    assert [v.text for v in verses] == ["text for Matt.17.1", "text for Matt.17.3"]


def test_a_file_import_still_refuses_its_blank_verse(tmp_path, loaded):
    source = tmp_path / "gapped.json"
    source.write_text(json.dumps(_gapped_payload()), encoding="utf-8")
    with pytest.raises(CorpusError, match="Matthew 17:2 is blank in the source"):
        corpus.import_corpus(
            loaded, code="niv", name="New International Version", source_path=source
        )
