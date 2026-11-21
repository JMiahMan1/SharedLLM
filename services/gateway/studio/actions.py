"""Every studio action, once: its request shape, what it does, and how it fails.

The HTTP routes (``routes.py``) and the model's tools (``tools.py``) are two doors
into the same room. Both validate a request with the models below, run the same
handler with the caller's own user name, and turn the same exceptions into the
same status codes, so a person pressing a button and the model in the workspace
chat cannot get different answers to the same request.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from services.gateway import workspace_api as ws
from services.gateway.studio import media, runtime, stl, works
from services.gateway.studio import profiles as profiles_mod


class StudioActionError(Exception):
    """A studio action failed; ``status`` is the HTTP code that names why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- request shapes ------------------------------------------------------------

class WorkspaceBody(BaseModel):
    workspace_id: str


class RunBody(WorkspaceBody):
    action: str = Field(default="test", description="install | test | build | lint")
    timeout: int = Field(default=runtime.DEFAULT_TIMEOUT_S, ge=10, le=1800)


class PreviewBody(WorkspaceBody):
    port: int | None = Field(default=None, ge=1, le=65535)


class VerifyBody(WorkspaceBody):
    run_tests: bool = Field(default=True, description="Also run the project's test step")
    timeout: int = Field(default=runtime.DEFAULT_TIMEOUT_S, ge=10, le=1800)


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
    replace: bool = Field(default=False, description="Overwrite an existing deck of the same title")


class GameBody(WorkspaceBody):
    title: str
    sprites: dict[str, str] | None = None
    replace: bool = Field(default=False, description="Overwrite an existing game of the same title")


class StlBody(WorkspaceBody):
    path: str = Field(..., description="Workspace-relative path to the .stl file")


class StlPreviewBody(StlBody):
    limit: int = Field(default=20_000, ge=100, le=100_000)


class StlTransformBody(StlBody):
    out_path: str | None = Field(
        default=None,
        description="Workspace-relative .stl to write (default: the first free <name>_edited[_N].stl)",
    )
    scale: float | list[float] = Field(default=1.0, description="Uniform factor, or [x, y, z]")
    rotate: list[float] | None = Field(default=None, description="Degrees about [x, y, z]")
    translate: list[float] | None = Field(default=None, description="Millimetres along [x, y, z]")
    fit: float | None = Field(default=None, description="Scale so the longest side is this")
    center: bool = Field(default=False, description="Put the bounding-box centre on the origin")
    format: str | None = Field(default=None, description="binary | ascii")
    replace: bool = Field(default=False, description="Overwrite an existing out_path")


# --- handlers ------------------------------------------------------------------

async def profile(body: WorkspaceBody, user: str) -> dict[str, Any]:
    entries = await runtime.profile_entries(body.workspace_id, user=user)
    payload = profiles_mod.detect(entries).to_dict()
    payload["all_stacks"] = profiles_mod.all_stacks(entries)
    return payload


async def diagnose(body: WorkspaceBody, user: str) -> dict[str, Any]:
    return await runtime.diagnose(body.workspace_id, user=user)


async def verify(body: VerifyBody, user: str) -> dict[str, Any]:
    return await runtime.verify(
        body.workspace_id, user=user, run_tests=body.run_tests, timeout=body.timeout
    )


async def run(body: RunBody, user: str) -> dict[str, Any]:
    result = await runtime.run_action(body.workspace_id, body.action, user=user, timeout=body.timeout)
    return result.to_dict()


async def preview(body: PreviewBody, user: str) -> dict[str, Any]:
    return await runtime.preview(body.workspace_id, user=user, port=body.port)


async def preview_stop(body: WorkspaceBody, user: str) -> dict[str, Any]:
    return await runtime.stop(body.workspace_id, user=user)


async def assets(body: WorkspaceBody, user: str) -> dict[str, Any]:
    return await media.manifest(body.workspace_id, user=user)


async def image(body: ImageBody, user: str) -> dict[str, Any]:
    asset = await media.generate_image(
        body.workspace_id, body.prompt, name=body.name, size=body.size, model=body.model, user=user
    )
    return asset.to_dict()


async def narration(body: NarrationBody, user: str) -> dict[str, Any]:
    asset = await media.narrate(body.workspace_id, body.text, name=body.name, voice=body.voice, user=user)
    return asset.to_dict()


async def deck(body: DeckBody, user: str) -> dict[str, Any]:
    built = await works.build_deck(
        body.workspace_id, body.markdown, title=body.title, footer=body.footer,
        replace=body.replace, user=user,
    )
    return built.to_dict()


async def game(body: GameBody, user: str) -> dict[str, Any]:
    built = await works.scaffold_game(
        body.workspace_id, body.title, sprites=body.sprites, replace=body.replace, user=user
    )
    return built.to_dict()


async def stl_inspect(body: StlBody, user: str) -> dict[str, Any]:
    return await stl.inspect(body.workspace_id, body.path, user=user)


async def stl_transform(body: StlTransformBody, user: str) -> dict[str, Any]:
    return await stl.transform(
        body.workspace_id,
        body.path,
        out_path=body.out_path,
        scale=body.scale,
        rotate=body.rotate,
        translate=body.translate,
        fit=body.fit,
        center=body.center,
        format=body.format,
        replace=body.replace,
        user=user,
    )


async def stl_preview(body: StlPreviewBody, user: str) -> dict[str, Any]:
    return await stl.preview(body.workspace_id, body.path, limit=body.limit, user=user)


# --- the table both doors read ---------------------------------------------------

Handler = Callable[[Any, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Action:
    name: str
    body: type[BaseModel]
    handler: Handler
    description: str
    hint: str


ACTIONS: dict[str, Action] = {
    action.name: action
    for action in (
        Action("profile", WorkspaceBody, profile,
               "Detect the workspace's stack and its exact install/test/build/lint/serve commands.",
               "payload fields: none beyond workspace_id."),
        Action("diagnose", WorkspaceBody, diagnose,
               "Run safe probes (user, HOME, cwd, bash, python3, git) to find out what works in the sandbox shell.",
               "payload fields: none beyond workspace_id."),
        Action("verify", VerifyBody, verify,
               "Prove the workspace works: diagnose the shell, run a one-line program, then the project's tests.",
               "payload fields: run_tests, timeout."),
        Action("run", RunBody, run,
               "Run the detected project's install, test, build or lint step in the sandbox and report the result.",
               "payload fields: action (install|test|build|lint), timeout."),
        Action("preview", PreviewBody, preview,
               "Start the project's dev server, publish its port and return the URL (stops any earlier preview).",
               "payload fields: port (optional)."),
        Action("preview_stop", WorkspaceBody, preview_stop,
               "Stop the preview server and close every port this workspace published.",
               "payload fields: none beyond workspace_id."),
        Action("assets", WorkspaceBody, assets,
               "List the art and audio made for this workspace, with the prompt and model that made each.",
               "payload fields: none beyond workspace_id."),
        Action("image", ImageBody, image,
               "Generate a picture with the household's image model into assets/ and record it in the manifest.",
               "payload fields: prompt, name, size, model."),
        Action("narration", NarrationBody, narration,
               "Speak text into assets/<name>.wav and record it in the manifest.",
               "payload fields: text, name, voice."),
        Action("deck", DeckBody, deck,
               "Render Markdown (slides separated by '---') into decks/<slug>/index.html with its source.",
               "payload fields: markdown, title, footer, replace (overwrite an existing deck)."),
        Action("game", GameBody, game,
               "Write a playable single-file canvas game to games/<slug>/index.html with a README.",
               "payload fields: title, sprites ({name: assets/path}), replace (overwrite an existing game)."),
        Action("stl_inspect", StlBody, stl_inspect,
               "Read an STL model and report triangles, size, volume, whether it is watertight, and every warning.",
               "payload fields: path (workspace-relative .stl)."),
        Action("stl_transform", StlTransformBody, stl_transform,
               "Rotate/fit/scale/centre/move an STL model into a NEW file and report the sizes before and after.",
               "payload fields: path, scale, rotate [x,y,z], translate [x,y,z], fit, center, format, out_path, replace."),
        Action("stl_preview", StlPreviewBody, stl_preview,
               "An indexed mesh (positions + faces) of an STL model for drawing it.",
               "payload fields: path, limit."),
    )
}


def error_for(exc: Exception) -> StudioActionError | None:
    """The status code for a studio failure, decided once for both doors."""
    if isinstance(exc, StudioActionError):
        return exc
    if isinstance(exc, works.WorkExists):
        return StudioActionError(409, str(exc))
    if isinstance(exc, (stl.StlError, works.WorkError)):
        return StudioActionError(400, str(exc))
    if isinstance(exc, runtime.ShellUnavailable):
        return StudioActionError(502, str(exc))
    if isinstance(exc, runtime.StudioError):
        return StudioActionError(400, str(exc))
    if isinstance(exc, media.MediaError):
        return StudioActionError(502, str(exc))
    if isinstance(exc, ws.WorkspaceConflict):
        return StudioActionError(409, str(exc))
    if isinstance(exc, ws.WorkspaceFileNotFound):
        return StudioActionError(404, str(exc))
    if isinstance(exc, ws.WorkspaceError):
        # The runtime's own refusal keeps its code (404 no such workspace, 403 no
        # capability, 400 bad path); a runtime that could not answer is a 502.
        status = exc.status if exc.status in (400, 403, 404, 409, 413) else 502
        return StudioActionError(status, str(exc))
    return None


async def invoke(name: str, body: BaseModel, user: str) -> dict[str, Any]:
    """Run one action as ``user``; a known failure becomes a :class:`StudioActionError`."""
    action = ACTIONS[name]
    if not isinstance(body, action.body):
        raise TypeError(f"{name} takes {action.body.__name__}, not {type(body).__name__}")
    try:
        return await action.handler(body, user)
    except Exception as exc:
        mapped = error_for(exc)
        if mapped is None:
            raise
        raise mapped from exc
