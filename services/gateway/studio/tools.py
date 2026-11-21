"""The studio as tools the model can call (single-turn chat and Raven).

The workspace chat was taught the ``/api/studio/*`` routes by RAG lessons, but no
tool could reach them: the sandbox cannot call the gateway, and the tool registry
had no row for them. These tools close that gap without a second implementation:
each one validates its payload with the route's own model and runs the route's
own handler (:mod:`.actions`) as the user whose turn it is, so the workspace
capability checks downstream are exactly the ones a person pressing the button
gets. The gateway runs the model, so the call is in-process: no self-HTTP, no
secret on a loopback request.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from services.gateway.studio import actions

#: Tool name -> action name. The names follow the registry's ``...Request`` style.
TOOL_ACTIONS: dict[str, str] = {
    "StudioProfileRequest": "profile",
    "StudioDiagnoseRequest": "diagnose",
    "StudioVerifyRequest": "verify",
    "StudioRunRequest": "run",
    "StudioPreviewRequest": "preview",
    "StudioPreviewStopRequest": "preview_stop",
    "StudioAssetsRequest": "assets",
    "StudioImageRequest": "image",
    "StudioNarrationRequest": "narration",
    "StudioDeckRequest": "deck",
    "StudioGameRequest": "game",
    "StlInspectRequest": "stl_inspect",
    "StlTransformRequest": "stl_transform",
}

#: The route each tool's action is also served at (for the registry and docs).
TOOL_PATHS: dict[str, str] = {
    "StudioProfileRequest": "/api/studio/profile",
    "StudioDiagnoseRequest": "/api/studio/diagnose",
    "StudioVerifyRequest": "/api/studio/verify",
    "StudioRunRequest": "/api/studio/run",
    "StudioPreviewRequest": "/api/studio/preview",
    "StudioPreviewStopRequest": "/api/studio/preview/stop",
    "StudioAssetsRequest": "/api/studio/assets/list",
    "StudioImageRequest": "/api/studio/assets/image",
    "StudioNarrationRequest": "/api/studio/assets/narration",
    "StudioDeckRequest": "/api/studio/deck",
    "StudioGameRequest": "/api/studio/game",
    "StlInspectRequest": "/api/studio/stl/inspect",
    "StlTransformRequest": "/api/studio/stl/transform",
}


def _normalize(name: str) -> str:
    return re.sub(r"[\s_]+", "", name or "").lower()


_BY_NORMALIZED: dict[str, str] = {_normalize(name): name for name in TOOL_ACTIONS}


def tool_for(name: str) -> str | None:
    """The canonical studio tool for any spelling of ``name``, or ``None``."""
    return _BY_NORMALIZED.get(_normalize(name))


async def call(
    name: str,
    payload: dict[str, Any],
    *,
    user: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run one studio tool as ``user`` and return the action's answer.

    ``workspace_id`` (the turn's workspace) wins over one the model named, as for
    every other workspace tool. A bad payload or a failed action is a
    :class:`actions.StudioActionError` with the same status and words the route
    would answer.
    """
    tool = tool_for(name)
    if tool is None:
        raise KeyError(f"{name} is not a studio tool")
    if not user:
        raise actions.StudioActionError(401, "a studio tool needs the acting user")
    action = actions.ACTIONS[TOOL_ACTIONS[tool]]
    fields = {k: v for k, v in (payload or {}).items() if k not in {"user_context", "user"}}
    if workspace_id:
        fields["workspace_id"] = workspace_id
    try:
        body = action.body.model_validate(fields)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'payload'}: {error['msg']}"
            for error in exc.errors()
        )
        raise actions.StudioActionError(400, f"{tool}: {problems}") from exc
    return await actions.invoke(action.name, body, user)


def summarize(name: str, result: dict[str, Any], *, limit: int = 4000) -> str:
    """The result as the model reads it: the whole answer, ``ok`` first.

    Never a bare "done": a failed test run is an answer the model must see.
    """
    tool = tool_for(name) or name
    ordered = {"ok": result["ok"]} if "ok" in result else {}
    ordered.update(result)
    text = json.dumps(ordered, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[: limit - 40] + f"... [{len(text) - limit + 40} chars cut]"
    return f"{tool} result: {text}"
