"""A sermon must be grounded, small-model-shaped, and honest about what it lacks.

The pipeline is deliberately several small calls (an outline, then one paragraph
per point) because that is what a modest local model can do without running out
of context. These tests pin the outline parser against the shapes models actually
return, the markdown the workspace stores, and the two honest-degradation paths:
no Bible service, and no labels in the outline.
"""
from __future__ import annotations

import pytest

from services.gateway.trainer import sermon as sermon_mod

OUTLINE = (
    "Title: The God Who Works\n"
    "Big idea: God is at work in what looks like ruin.\n"
    "Point 1: He works in the waiting\n"
    "Point 2: He works through the harm\n"
    "Application: Name one hard thing and thank God for his hidden work.\n"
    "Prayer: Father, teach us to trust your slow work. Amen.\n"
)


@pytest.fixture
def workspace(monkeypatch):
    written: dict[str, str] = {}

    async def fake_write(workspace_id, path, content, *, expected_sha256=None, user="default", **kwargs):
        written[path] = content
        return {"status": "SUCCESS"}

    monkeypatch.setattr(sermon_mod.ws, "write_file", fake_write)
    return written


@pytest.fixture
def scripture(monkeypatch):
    async def fake_post(endpoint, payload, timeout=120.0):
        assert endpoint == "/execute/bible"
        assert payload["action"] == "read"
        return {"status": "SUCCESS", "message": "Romans 8:28 And we know that all things work together for good."}

    monkeypatch.setattr(sermon_mod, "_execution_post", fake_post)
    return fake_post


def test_slugify_makes_a_file_name():
    assert sermon_mod.slugify("The God Who Works!") == "the-god-who-works"
    assert sermon_mod.slugify("   ") == "sermon"
    assert len(sermon_mod.slugify("x" * 200)) <= 60


def test_the_outline_parser_accepts_the_shapes_models_return():
    labelled = sermon_mod.parse_outline(OUTLINE)
    assert labelled["title"] == "The God Who Works"
    assert labelled["big idea"] == "God is at work in what looks like ruin."
    assert labelled["point 1"] == "He works in the waiting"
    assert labelled["point 2"] == "He works through the harm"
    assert labelled["prayer"].startswith("Father")

    bold = sermon_mod.parse_outline("**Title:** Mercy\n**Big idea:** God runs.\n")
    assert bold["title"] == "Mercy"


def test_an_unlabelled_outline_is_kept_as_prose():
    parts = sermon_mod.parse_outline("Mercy is the whole Bible in one word.")

    assert parts == {"body": "Mercy is the whole Bible in one word."}


def test_points_are_in_numbered_order():
    parts = sermon_mod.parse_outline(OUTLINE)

    assert [heading for heading, _ in sermon_mod.outline_points(parts)] == [
        "He works in the waiting",
        "He works through the harm",
    ]


def test_the_markdown_cites_the_passage_and_admits_missing_scripture():
    with_text = sermon_mod.Sermon(
        title="T", passage="Romans 8:28", translation="kjv",
        scripture="Romans 8:28 And we know...", big_idea="God works.",
        points=[{"heading": "Waiting", "text": "Body."}], model="coder",
    )
    text = sermon_mod.render_markdown(with_text)
    assert "# T" in text and "**Text:** Romans 8:28 (kjv)" in text
    assert "> Romans 8:28 And we know..." in text
    assert "## 1. Waiting" in text
    assert "local model (coder)" in text

    without = sermon_mod.render_markdown(
        sermon_mod.Sermon(title="T", passage="Romans 8:28", translation="kjv")
    )
    assert "Scripture was not fetched" in without


async def test_compose_writes_the_sermon_into_the_workspace(workspace, scripture):
    answers = [OUTLINE, "Paragraph one.", "Paragraph two."]

    async def fake_ask(prompt, max_tokens=100):
        return answers.pop(0)

    drafted = await sermon_mod.compose_sermon(
        "sermons", passage="Romans 8:28", ask=fake_ask, user="alice"
    )

    assert drafted.title == "The God Who Works"
    assert drafted.path == "sermons/the-god-who-works.md"
    assert drafted.words > 50
    assert len(drafted.points) == 2
    assert drafted.scripture.startswith("Romans 8:28")
    assert "sermons/the-god-who-works.md" in workspace
    assert "## 2. He works through the harm" in workspace["sermons/the-god-who-works.md"]


async def test_compose_still_writes_when_the_bible_service_is_down(workspace, monkeypatch):
    async def broken_post(endpoint, payload, timeout=120.0):
        raise ConnectionError("bible container is down")

    monkeypatch.setattr(sermon_mod, "_execution_post", broken_post)
    answers = [OUTLINE, "Paragraph one.", "Paragraph two."]

    async def fake_ask(prompt, max_tokens=100):
        return answers.pop(0)

    drafted = await sermon_mod.compose_sermon("sermons", passage="Romans 8:28", ask=fake_ask)

    assert drafted.scripture == ""
    assert any("scripture fetch failed" in note for note in drafted.notes)
    assert "Scripture was not fetched" in workspace[drafted.path]


async def test_an_unlabelled_outline_is_still_saved_as_work(workspace, scripture):
    async def fake_ask(prompt, max_tokens=100):
        return "Mercy is the whole Bible in one word, and it is for you today."

    drafted = await sermon_mod.compose_sermon("sermons", passage="Romans 8:28", ask=fake_ask)

    assert len(drafted.points) == 1
    assert drafted.points[0]["heading"] == "Draft"
    assert any("unlabelled" in note for note in drafted.notes)
    assert drafted.path in workspace


async def test_a_passage_is_required():
    with pytest.raises(sermon_mod.SermonError):
        await sermon_mod.compose_sermon("sermons", passage="  ")


async def test_illustrating_edits_the_image_in_the_workspace(monkeypatch):
    seen: dict = {}

    async def fake_post(endpoint, payload, timeout=120.0):
        seen["endpoint"] = endpoint
        seen["payload"] = payload
        return {"status": "SUCCESS", "detail": {"output_path": "art/header.png", "model": "qwen-edit"}}

    monkeypatch.setattr(sermon_mod, "_execution_post", fake_post)

    result = await sermon_mod.illustrate(
        "sermons", image_path="art/base.png", prompt="make it a watercolour", user="alice"
    )

    assert seen["endpoint"] == "/execute/image_edit"
    assert seen["payload"]["workspace_id"] == "sermons"
    assert seen["payload"]["image_path"] == "art/base.png"
    assert seen["payload"]["user_context"] == {"user": "alice"}
    assert result["status"] == "SUCCESS"
    assert result["output_path"] == "art/header.png"


async def test_illustrating_needs_an_image_and_a_prompt():
    with pytest.raises(sermon_mod.SermonError):
        await sermon_mod.illustrate("sermons", image_path="", prompt="x")
