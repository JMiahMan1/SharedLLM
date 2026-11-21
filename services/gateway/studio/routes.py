"""``/api/studio`` -- the Workspace's build, test, preview and media surface.

A workspace is not only a folder of files: it is where code runs, where tests
prove it, where a dev server is previewed on a phone, and where the art and audio
for a game or a deck are made. These routes are the same for a person pressing a
button and for the model in the workspace chat: both go through
:mod:`services.gateway.studio.actions` (the model through ``studio.tools``).

Policy follows the rest of the gateway: every route needs a signed-in user, and
what the caller may *do* is decided downstream by the workspace's own
capabilities (the sandbox shell and the Workspace API) for *that* user, never
here. Nothing in this module accepts a free-form command: the studio runs the
step the detected project profile names.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.gateway.studio import actions
from services.gateway.studio.actions import (
    DeckBody,
    GameBody,
    ImageBody,
    NarrationBody,
    PreviewBody,
    RunBody,
    StlBody,
    StlPreviewBody,
    StlTransformBody,
    VerifyBody,
    WorkspaceBody,
)

log = logging.getLogger("gateway.studio.routes")

router = APIRouter(prefix="/api/studio")


async def _user(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return await _require_authenticated(request)


async def _act(request: Request, name: str, body: BaseModel) -> dict[str, Any]:
    """Authenticate, run the shared action as the caller, map its failure."""
    user = await _user(request)
    try:
        return await actions.invoke(name, body, user)
    except actions.StudioActionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/profile")
async def get_profile(request: Request, workspace_id: str) -> dict[str, Any]:
    """What kind of project this workspace holds, and what it can run."""
    return await _act(request, "profile", WorkspaceBody(workspace_id=workspace_id))


@router.post("/profile")
async def post_profile(request: Request, body: WorkspaceBody) -> dict[str, Any]:
    """The same as ``GET /profile``, for callers (the model's tools) that only POST."""
    return await _act(request, "profile", body)


@router.post("/diagnose")
async def diagnose_shell(request: Request, body: WorkspaceBody) -> dict[str, Any]:
    """Prove which parts of the sandbox shell work, and name what does not."""
    return await _act(request, "diagnose", body)


@router.post("/verify")
async def verify_workspace(request: Request, body: VerifyBody) -> dict[str, Any]:
    """Prove the workspace works: the shell, a real program, and its tests."""
    return await _act(request, "verify", body)


@router.post("/run")
async def run_step(request: Request, body: RunBody) -> dict[str, Any]:
    """Run the profile's install / test / build / lint step in the sandbox."""
    return await _act(request, "run", body)


@router.post("/preview")
async def start_preview(request: Request, body: PreviewBody) -> dict[str, Any]:
    """Start the dev server (stopping any earlier one), publish its port, check it answers."""
    return await _act(request, "preview", body)


@router.post("/preview/stop")
async def stop_preview(request: Request, body: WorkspaceBody) -> dict[str, Any]:
    """Stop the preview server and close every port this workspace published."""
    return await _act(request, "preview_stop", body)


@router.get("/assets")
async def list_assets(request: Request, workspace_id: str) -> dict[str, Any]:
    """The art and audio made for this workspace, with the prompts that made them."""
    return await _act(request, "assets", WorkspaceBody(workspace_id=workspace_id))


@router.post("/assets/list")
async def post_list_assets(request: Request, body: WorkspaceBody) -> dict[str, Any]:
    """The same as ``GET /assets``, for callers that only POST."""
    return await _act(request, "assets", body)


@router.post("/assets/image")
async def make_image(request: Request, body: ImageBody) -> dict[str, Any]:
    """Generate a picture with the household's image model, into the workspace."""
    return await _act(request, "image", body)


@router.post("/assets/narration")
async def make_narration(request: Request, body: NarrationBody) -> dict[str, Any]:
    """Speak text into an audio file in the workspace (a deck's narration, a story)."""
    return await _act(request, "narration", body)


@router.post("/deck")
async def build_deck(request: Request, body: DeckBody) -> dict[str, Any]:
    """Markdown in, a self-contained HTML deck out (offline, phone-friendly)."""
    return await _act(request, "deck", body)


@router.post("/game")
async def scaffold_game(request: Request, body: GameBody) -> dict[str, Any]:
    """A playable single-file game, ready to preview on a phone."""
    return await _act(request, "game", body)


@router.post("/stl/inspect")
async def inspect_stl(request: Request, body: StlBody) -> dict[str, Any]:
    """Open a 3D model and describe it: size, volume, and what is wrong with it."""
    return await _act(request, "stl_inspect", body)


@router.post("/stl/transform")
async def transform_stl(request: Request, body: StlTransformBody) -> dict[str, Any]:
    """Scale / rotate / centre / move a model and write the result as a new file."""
    return await _act(request, "stl_transform", body)


@router.get("/stl/preview")
async def preview_stl(
    request: Request, workspace_id: str, path: str, limit: int = 20_000
) -> dict[str, Any]:
    """An indexed mesh (positions + faces) the browser can draw."""
    body = StlPreviewBody(workspace_id=workspace_id, path=path, limit=max(100, min(limit, 100_000)))
    return await _act(request, "stl_preview", body)
