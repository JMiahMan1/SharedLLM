"""What the studio writes must be openable, editable and re-generatable.

A deck keeps its Markdown next to the HTML; a game keeps a README that says which
line to change. These tests pin the files, the paths, and the two refusals: an
empty deck and a workspace that cannot be written.
"""
from __future__ import annotations

import json

import pytest

from services.gateway import workspace_api as ws
from services.gateway.studio import works


@pytest.fixture
def workspace(monkeypatch):
    """Files by path; a create-only write over an existing file is refused, like the runtime."""
    files: dict[str, str] = {}

    async def fake_write(workspace_id, path, content, *, expected_sha256=None, user, create_parents=True):
        assert user == "u1"
        if expected_sha256 == ws.CREATE_ONLY and path in files:
            raise ws.WorkspaceConflict(f"Could not write {path}: exists", 409)
        files[path] = content
        return {"status": "SUCCESS"}

    monkeypatch.setattr(works.ws, "write_file", fake_write)
    return files


async def test_a_deck_keeps_its_source_beside_the_html(workspace):
    built = await works.build_deck(
        "w1", "# One\n- a\n\n---\n\n# Two\n- b", title="Series: Grace", user="u1"
    )

    assert built.path == "decks/series-grace/index.html"
    assert built.slides == 2
    assert workspace["decks/series-grace/source.md"].startswith("# One")
    assert "<h1>One</h1>" in workspace["decks/series-grace/index.html"]


async def test_an_empty_deck_is_refused(workspace):
    with pytest.raises(works.WorkError) as excinfo:
        await works.build_deck("w1", "   \n\n---\n\n   ", user="u1")

    assert "separated by '---'" in str(excinfo.value)
    assert workspace == {}


async def test_the_deck_title_falls_back_to_the_first_slide(workspace):
    built = await works.build_deck("w1", "# Romans 8\n- all things", user="u1")

    assert built.title == "Romans 8"
    assert built.path == "decks/romans-8/index.html"


async def test_a_game_is_one_file_and_a_readme_that_names_the_hooks(workspace):
    built = await works.scaffold_game(
        "w1", "Star Catcher", sprites={"player": "assets/hero.png", "star": "assets/star.png"}, user="u1"
    )

    page = workspace["games/star-catcher/index.html"]
    readme = workspace["games/star-catcher/README.md"]
    assert built.path == "games/star-catcher/index.html"
    assert built.assets == ["assets/hero.png", "assets/star.png"]
    assert json.loads(page.split('id="sprites">')[1].split("</script>")[0]) == {
        "player": "assets/hero.png",
        "star": "assets/star.png",
    }
    assert "Left / right arrows" in readme
    assert "assets/hero.png" in readme
    assert "assets/manifest.json" in readme


async def test_a_game_without_art_still_says_how_to_add_some(workspace):
    await works.scaffold_game("w1", "Untitled", user="u1")

    readme = workspace["games/untitled/README.md"]
    assert "the game draws simple shapes" in readme
    assert "POST /api/studio/assets/image" in readme


async def test_a_workspace_that_cannot_be_written_reports_it(monkeypatch):
    async def refuse(workspace_id, path, content, *, expected_sha256=None, user, create_parents=True):
        raise ws.WorkspaceError("Could not write decks/x/index.html: 403")

    monkeypatch.setattr(works.ws, "write_file", refuse)

    with pytest.raises(ws.WorkspaceError) as excinfo:
        await works.build_deck("w1", "# One", user="u1")

    assert "403" in str(excinfo.value)


async def test_rebuilding_a_deck_refuses_to_overwrite_it_unless_asked(workspace):
    await works.build_deck("w1", "# One\n- a", user="u1")
    workspace["decks/one/source.md"] = "# One\n- edited by hand"

    with pytest.raises(works.WorkExists, match="replace"):
        await works.build_deck("w1", "# One\n- b", user="u1")
    assert workspace["decks/one/source.md"] == "# One\n- edited by hand"

    await works.build_deck("w1", "# One\n- b", replace=True, user="u1")
    assert workspace["decks/one/source.md"] == "# One\n- b"


async def test_a_game_is_never_scaffolded_over_an_edited_one(workspace):
    await works.scaffold_game("w1", "Star Catcher", user="u1")
    workspace["games/star-catcher/index.html"] = "<!-- my changes -->"

    with pytest.raises(works.WorkExists):
        await works.scaffold_game("w1", "Star Catcher", user="u1")
    assert workspace["games/star-catcher/index.html"] == "<!-- my changes -->"


async def test_a_title_and_sprite_paths_cannot_inject_markup_or_script(workspace):
    await works.scaffold_game(
        "w1",
        'Catch "<img src=x onerror=alert(1)>"',
        sprites={"player": "assets/a.png</script><script>alert(2)</script>", "star": "a&b.png"},
        user="u1",
    )

    page = next(v for k, v in workspace.items() if k.endswith("index.html"))
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert 'data-title="Catch &quot;' in page, "an attribute value cannot be closed by the title"
    assert "</script><script>alert(2)" not in page
    sprites = page.split('id="sprites">')[1].split("</script>")[0]
    assert json.loads(sprites) == {"player": "assets/a.png</script><script>alert(2)</script>", "star": "a&b.png"}
