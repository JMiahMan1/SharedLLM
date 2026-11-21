"""What the studio writes into a workspace: a deck, a game, and how to change them.

Everything lands as plain files a person can open, edit and re-generate: the deck
keeps its Markdown source next to the HTML, and the game keeps a README that says
which file to edit. Generated art is referenced from ``assets/`` so the manifest
explains where it came from.
"""
from __future__ import annotations

import html
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from services.gateway import workspace_api as ws
from services.gateway.studio import deck as deck_mod
from services.gateway.studio import templates
from services.gateway.studio.profiles import slug

log = logging.getLogger("gateway.studio.works")

DECKS_DIR = "decks"
GAMES_DIR = "games"
ASSETS_DIR = "assets"


class WorkError(RuntimeError):
    """The studio could not write the work, with a reason a person can read."""


@dataclass
class Built:
    kind: str
    path: str
    title: str
    files: list[str] = field(default_factory=list)
    slides: int = 0
    assets: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def game_readme(title: str, *, sprites: dict[str, str] | None = None) -> str:
    """How to play, and where to change it. Written for the next person."""
    art = "\n".join(f"- `{path}` — {name}" for name, path in (sprites or {}).items()) or (
        "- none yet: the game draws simple shapes until you generate art "
        "(`POST /api/studio/assets/image`) and name it in `SPRITES` below"
    )
    return f"""# {title}

A single-file canvas game. No build step, no dependencies, no network: open
`index.html` (or preview it from the studio) and it runs.

## Play

- **Left / right arrows** or **A / D** to move
- **Space / Enter**, a **tap** or a **click** to start and to play again
- On a phone, drag along the bottom: the catcher follows your finger

## Change it

Everything is in `index.html`:

| What | Where |
|---|---|
| The title | `<body data-title="...">` and `<title>` |
| Art | `SPRITES` (the JSON block near the bottom): `{{"player": "assets/hero.png", "star": "assets/star.png"}}` |
| Speed, spawn rate, lives | `spawn()` and `reset()` in the script |
| Scoring | the collision block in `step()` |

## Art used

{art}

## Make more art

Ask the studio for a picture, then point `SPRITES` at it:

```
POST /api/studio/assets/image
{{"workspace_id": "<this workspace>", "prompt": "a friendly blue robot, flat cartoon, transparent background", "name": "hero"}}
```

The file lands in `assets/` and is recorded in `assets/manifest.json` with the
prompt and model that made it.
"""


class WorkExists(WorkError):
    """The work is already in the workspace and the caller did not ask to replace it."""


async def _write_work(
    workspace_id: str, files: dict[str, str], *, replace: bool, user: str
) -> None:
    """Write a work's files, refusing to overwrite any of them unless ``replace``.

    A person edits a generated game or deck by hand (the README tells them to);
    re-running the scaffold must not silently throw that work away. Each file is
    created with ``expected_sha256="new"``, so the Workspace Runtime refuses the
    write if the file appeared in between, not only if this check saw it.
    """
    for path, content in files.items():
        try:
            await ws.write_file(
                workspace_id,
                path,
                content,
                user=user,
                expected_sha256=None if replace else ws.CREATE_ONLY,
            )
        except ws.WorkspaceConflict as exc:
            raise WorkExists(
                f"{path} already exists. Choose another title, or rebuild with replace=true "
                "to overwrite it (hand edits to it will be lost)."
            ) from exc


async def build_deck(
    workspace_id: str,
    markdown: str,
    *,
    title: str = "",
    footer: str = "",
    replace: bool = False,
    user: str,
) -> Built:
    """Render a Markdown deck into ``decks/<slug>/index.html`` (+ its source)."""
    slides = deck_mod.parse(markdown)
    if not slides:
        raise WorkError("the deck is empty: write slides separated by '---'")
    heading = title.strip() or slides[0].title or "Presentation"
    folder = f"{DECKS_DIR}/{slug(heading)}"
    page = deck_mod.render(slides, heading, footer=footer)

    files = {f"{folder}/index.html": page, f"{folder}/source.md": markdown}
    await _write_work(workspace_id, files, replace=replace, user=user)
    return Built(
        kind="deck",
        path=f"{folder}/index.html",
        title=heading,
        files=list(files),
        slides=len(slides),
        assets=[slide.image for slide in slides if slide.image],
        message=f"{len(slides)} slides, offline, one file",
    )


def game_page(title: str, sprites: dict[str, str] | None = None) -> str:
    """The game's ``index.html``, with the title and sprite paths escaped for where they land."""
    return templates.render(
        templates.GAME_HTML,
        title=html.escape(title, quote=True),
        sprites=templates.script_json({k: v for k, v in (sprites or {}).items() if v}),
        js=templates.GAME_JS,
    )


async def scaffold_game(
    workspace_id: str,
    title: str,
    *,
    sprites: dict[str, str] | None = None,
    replace: bool = False,
    user: str,
) -> Built:
    """Write a playable game to ``games/<slug>/index.html`` with a README."""
    heading = (title or "").strip() or "Untitled Game"
    folder = f"{GAMES_DIR}/{slug(heading)}"
    files = {
        f"{folder}/index.html": game_page(heading, sprites),
        f"{folder}/README.md": game_readme(heading, sprites=sprites),
    }
    await _write_work(workspace_id, files, replace=replace, user=user)
    return Built(
        kind="game",
        path=f"{folder}/index.html",
        title=heading,
        files=list(files),
        assets=[path for path in (sprites or {}).values() if path],
        message="one file, keyboard and touch, ready to play",
    )
