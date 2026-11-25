"""Prompt settings follow prompts/*.md unless an admin edited them (prompt_seed.py)."""
import logging

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import prompt_seed
from services.identity.models import GlobalSetting, PromptSeed
from services.identity.prompt_seed import prompt_hash, sync_prompts

KEY = "example_tool_guide"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="prompts")
def prompts_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_seed, "PROMPTS_DIR", tmp_path)

    def write(text: str, key: str = KEY):
        (tmp_path / f"{key}.md").write_text(text, encoding="utf-8")

    return write


def _live(session: Session, key: str = KEY) -> str | None:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    return row.value if row else None


def _set_live(session: Session, value: str, key: str = KEY) -> None:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if row is None:
        row = GlobalSetting(key=key, value=value)
    row.value = value
    session.add(row)
    session.commit()


def _sync(session: Session, **kwargs) -> dict[str, str]:
    actions = sync_prompts(session, **kwargs)
    session.commit()
    return actions


def test_a_missing_prompt_is_seeded_and_its_hash_recorded(session, prompts):
    prompts("Tools: A, B\n")
    assert _sync(session) == {KEY: "seeded"}
    assert _live(session) == "Tools: A, B"
    assert session.get(PromptSeed, KEY).seeded_hash == prompt_hash("Tools: A, B")


def test_an_untouched_prompt_follows_the_repo(session, prompts, caplog):
    prompts("Tools: A, B")
    _sync(session)
    prompts("Tools: A, B, TodoListRequest, AudiobookshelfRequest")
    with caplog.at_level(logging.INFO, logger=prompt_seed.log.name):
        assert _sync(session) == {KEY: "updated"}
    assert _live(session) == "Tools: A, B, TodoListRequest, AudiobookshelfRequest"
    assert session.get(PromptSeed, KEY).seeded_hash == prompt_hash("Tools: A, B, TodoListRequest, AudiobookshelfRequest")
    assert f"updated prompt {KEY} from the repo" in caplog.text


def test_an_unchanged_file_changes_nothing(session, prompts):
    prompts("Tools: A")
    _sync(session)
    assert _sync(session) == {KEY: "current"}
    assert _live(session) == "Tools: A"


def test_an_edited_prompt_is_kept_and_the_newer_repo_version_logged_once(session, prompts, caplog):
    prompts("Tools: A")
    _sync(session)
    _set_live(session, "Tools: A (house wording)")
    prompts("Tools: A, B")
    with caplog.at_level(logging.INFO, logger=prompt_seed.log.name):
        assert _sync(session) == {KEY: "edited"}
        assert _sync(session) == {KEY: "edited"}
    assert _live(session) == "Tools: A (house wording)"
    newer = f"prompt {KEY} was edited in settings; the repo has a newer version"
    assert caplog.text.count(newer) == 1
    assert session.get(PromptSeed, KEY).seeded_hash == prompt_hash("Tools: A")

    # A later repo version is reported again, once.
    caplog.clear()
    prompts("Tools: A, B, C")
    with caplog.at_level(logging.INFO, logger=prompt_seed.log.name):
        _sync(session)
        _sync(session)
    assert caplog.text.count(newer) == 1


def test_an_edit_while_the_repo_is_unchanged_is_not_reported(session, prompts, caplog):
    prompts("Tools: A")
    _sync(session)
    _set_live(session, "Tools: A (house wording)")
    with caplog.at_level(logging.INFO, logger=prompt_seed.log.name):
        assert _sync(session) == {KEY: "edited"}
    assert "newer version" not in caplog.text
    status = prompt_seed.prompt_statuses(session)[0]
    assert status["state"] == "edited"
    assert status["repo_has_newer_version"] is False


def test_an_edit_back_to_the_repo_text_follows_the_repo_again(session, prompts):
    prompts("Tools: A")
    _sync(session)
    _set_live(session, "Tools: A (house wording)")
    prompts("Tools: A, B")
    _sync(session)
    _set_live(session, "Tools: A, B")
    assert _sync(session) == {KEY: "current"}
    prompts("Tools: A, B, C")
    assert _sync(session) == {KEY: "updated"}
    assert _live(session) == "Tools: A, B, C"


def test_a_row_from_before_the_record_matching_the_repo_gets_its_hash(session, prompts):
    _set_live(session, "Tools: A, B\n")
    prompts("Tools: A, B")
    assert _sync(session) == {KEY: "current"}
    assert session.get(PromptSeed, KEY).seeded_hash == prompt_hash("Tools: A, B")
    prompts("Tools: A, B, C")
    assert _sync(session) == {KEY: "updated"}
    assert _live(session) == "Tools: A, B, C"


def test_a_row_from_before_the_record_that_differs_counts_as_edited(session, prompts, caplog):
    _set_live(session, "Tools: A (an old seed)")
    prompts("Tools: A, B")
    with caplog.at_level(logging.INFO, logger=prompt_seed.log.name):
        assert _sync(session) == {KEY: "edited"}
    assert _live(session) == "Tools: A (an old seed)"
    assert session.get(PromptSeed, KEY).seeded_hash is None
    assert "the repo has a newer version" in caplog.text
    status = prompt_seed.prompt_statuses(session)[0]
    assert status["state"] == "edited"
    assert status["repo_has_newer_version"] is True
    # Still edited on the next start: an unknown seed is never taken as untouched.
    assert _sync(session) == {KEY: "edited"}


def test_whitespace_only_differences_are_equal(session, prompts):
    prompts("Line one\nLine two\n")
    _sync(session)
    _set_live(session, "Line one  \r\nLine two\n\n")
    assert _sync(session) == {KEY: "current"}
    prompts("Line one\nLine two\nLine three\n")
    assert _sync(session) == {KEY: "updated"}


def test_force_overwrites_an_edited_prompt(session, prompts):
    prompts("Tools: A")
    _sync(session)
    _set_live(session, "Tools: A (house wording)")
    prompts("Tools: A, B")
    assert _sync(session, force=True) == {KEY: "forced"}
    assert _live(session) == "Tools: A, B"
    assert session.get(PromptSeed, KEY).seeded_hash == prompt_hash("Tools: A, B")


def test_readme_and_empty_files_are_not_prompts(session, prompts):
    prompts("About these files", key="README")
    prompts("   \n", key="empty_prompt")
    prompts("Tools: A")
    assert _sync(session) == {KEY: "seeded"}
    assert _live(session, "README") is None
    assert _live(session, "empty_prompt") is None


def test_seed_from_env_runs_the_prompt_sync(session, prompts):
    from services.identity.seed import seed_from_env

    prompts("Tools: A")
    seed_from_env(session)
    prompts("Tools: A, B")
    seed_from_env(session)
    assert _live(session) == "Tools: A, B"


def test_the_shipped_prompts_are_found():
    shipped = prompt_seed.repo_prompts()
    assert "single_turn_tool_guide" in shipped
    assert "README" not in shipped


# Admin API

@pytest.fixture(name="client")
def client_fixture(session: Session):
    from services.identity.main import app, get_session, require_admin_or_internal

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    yield TestClient(app)
    app.dependency_overrides = {}


def test_the_admin_api_reports_view_and_replaces_an_edited_prompt(session, prompts, client):
    prompts("Tools: A")
    _sync(session)
    _set_live(session, "Tools: A (house wording)")
    prompts("Tools: A, B")
    _sync(session)

    listed = client.get("/api/admin/prompts").json()["prompts"]
    assert [(p["key"], p["state"], p["repo_has_newer_version"]) for p in listed] == [(KEY, "edited", True)]

    view = client.get(f"/api/admin/prompts/{KEY}").json()
    assert view["live"] == "Tools: A (house wording)"
    assert view["repo"] == "Tools: A, B"
    assert view["repo_has_newer_version"] is True

    replaced = client.post(f"/api/admin/prompts/{KEY}/replace")
    assert replaced.status_code == 200
    assert replaced.json()["state"] == "current"
    assert _live(session) == "Tools: A, B"

    # Replaced, it follows the repo again.
    prompts("Tools: A, B, C")
    assert _sync(session) == {KEY: "updated"}


def test_the_admin_api_404s_for_a_prompt_the_repo_does_not_have(prompts, client):
    prompts("Tools: A")
    assert client.get("/api/admin/prompts/not_a_prompt").status_code == 404
    assert client.post("/api/admin/prompts/not_a_prompt/replace").status_code == 404
