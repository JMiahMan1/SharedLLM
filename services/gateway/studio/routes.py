"""``/api/studio`` -- the Workspace's build, test, preview and media surface.

A workspace is not only a folder of files: it is where code runs, where tests
prove it, where a dev server is previewed on a phone, and where the art and audio
for a game or a deck are made. These routes are the same for a person pressing a
button and for the model in the workspace chat.

Policy follows the rest of the gateway: every route needs a signed-in user, and
what the caller may *do* is decided downstream by the workspace's own
capabilities (the sandbox shell and the Workspace API), never here. Nothing in
this module accepts a free-form command: the studio runs the step the detected
project profile names.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.gateway import workspace_api as ws
from services.gateway.studio import media, runtime, works
from services.gateway.studio import profiles as profiles_mod

log = logging.getLogger("gateway.studio.routes")

router = APIRouter(prefix="/api/studio")


class WorkspaceBody(BaseModel):
    workspace_id: str


class RunBody(WorkspaceBody):
    action: str = Field(default="test", description="install | test | build | lint")
    timeout: int = Field(default=runtime.DEFAULT_TIMEOUT_S, ge=10, le=1800)


class PreviewBody(WorkspaceBody):
    port: int | None = Field(default=None, ge=1, le=65535)


class ImageBody(WorkspaceBody):
    prompt: str
    name: str | None = None
    size: str = Field(default=media.DEFAULT_SIZE)
    model: str | None = None


class NarrationBody(WorkspaceBody):
    text: str
    name: str = "narration"
    voice: str | None = None


class DeckBody(WorkspaceBody):
    markdown: str
    title: str = ""
    footer: str = ""


class GameBody(WorkspaceBody):
    title: str
    sprites: dict[str, str] | None = None


async def _user(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return await _require_authenticated(request)


@router.get("/profile")
async def get_profile(request: Request, workspace_id: str) -> dict[str, Any]:
    """What kind of project this workspace holds, and what it can run."""
    await _user(request)
    try:
        entries = await ws.list_files(workspace_id, ".", recursive=True, max_depth=3, max_entries=600)
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = profiles_mod.detect(entries).to_dict()
    payload["all_stacks"] = profiles_mod.all_stacks(entries)
    return payload


@router.post("/run")
async def run_step(request: Request, body: RunBody) -> dict[str, Any]:
    """Run the profile's install / test / build / lint step in the sandbox."""
    user = await _user(request)
    try:
        result = await runtime.run_action(
            body.workspace_id, body.action, user=user, timeout=body.timeout
        )
    except runtime.StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/preview")
async def start_preview(request: Request, body: PreviewBody) -> dict[str, Any]:
    """Start the dev server, publish its port, and say whether it answered."""
    user = await _user(request)
    try:
        return await runtime.preview(body.workspace_id, user=user, port=body.port)
    except runtime.StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/preview/stop")
async def stop_preview(request: Request, body: WorkspaceBody) -> dict[str, Any]:
    """Stop the preview server and close every port this workspace published."""
    user = await _user(request)
    return await runtime.stop(body.workspace_id, user=user)


@router.get("/assets")
async def list_assets(request: Request, workspace_id: str) -> dict[str, Any]:
    """The art and audio made for this workspace, with the prompts that made them."""
    await _user(request)
    return await media.manifest(workspace_id)


@router.post("/assets/image")
async def make_image(request: Request, body: ImageBody) -> dict[str, Any]:
    """Generate a picture with the household's image model, into the workspace."""
    user = await _user(request)
    try:
        asset = await media.generate_image(
            body.workspace_id,
            body.prompt,
            name=body.name,
            size=body.size,
            model=body.model,
            user=user,
        )
    except media.MediaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return asset.to_dict()


@router.post("/assets/narration")
async def make_narration(request: Request, body: NarrationBody) -> dict[str, Any]:
    """Speak text into an audio file in the workspace (a deck's narration, a story)."""
    user = await _user(request)
    try:
        asset = await media.narrate(
            body.workspace_id, body.text, name=body.name, voice=body.voice, user=user
        )
    except media.MediaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return asset.to_dict()


@router.post("/deck")
async def build_deck(request: Request, body: DeckBody) -> dict[str, Any]:
    """Markdown in, a self-contained HTML deck out (offline, phone-friendly)."""
    user = await _user(request)
    try:
        built = await works.build_deck(
            body.workspace_id, body.markdown, title=body.title, footer=body.footer, user=user
        )
    except works.WorkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return built.to_dict()


@router.post("/game")
async def scaffold_game(request: Request, body: GameBody) -> dict[str, Any]:
    """A playable single-file game, ready to preview on a phone."""
    user = await _user(request)
    try:
        built = await works.scaffold_game(
            body.workspace_id, body.title, sprites=body.sprites, user=user
        )
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return built.to_dict()
