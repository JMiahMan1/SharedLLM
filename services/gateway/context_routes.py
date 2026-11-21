"""Admin views of what goes into the model's prompts and where it comes from.

``GET /api/admin/prompt-budget``  per-path, per-section prompt sizes (``prompt_budget``).
``GET /api/admin/knowledge-map``   the knowledge map each surface and role is given.
``/api/admin/tool-cache``          the tool-result cache: freshness, hit rate, tuning, invalidation.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from services.gateway import prompt_budget

router = APIRouter()


async def _admin(request: Request) -> str:
    from services.gateway.main import _require_admin

    return await _require_admin(request)


@router.get("/api/admin/prompt-budget")
async def get_prompt_budget(request: Request, recent: int = Query(20, ge=0, le=prompt_budget.RECENT_MAX)):
    """Prompt size by path and section over the last requests (estimated tokens = chars / 4)."""
    await _admin(request)
    return prompt_budget.snapshot(recent=recent)


@router.delete("/api/admin/prompt-budget")
async def reset_prompt_budget(request: Request):
    """Start a new measurement window (for a before/after comparison)."""
    await _admin(request)
    prompt_budget.reset()
    return {"status": "SUCCESS"}


@router.get("/api/admin/knowledge-map")
async def get_knowledge_map(request: Request, surface: str = Query("single_turn"), admin: bool = Query(False)):
    """The map exactly as a model on ``surface`` sees it, for an admin or a household member."""
    await _admin(request)
    from fastapi import HTTPException

    from services.gateway.knowledge_map import knowledge_map_for
    from services.gateway.prompts import PROMPT_SINGLE_TURN_TOOL_GUIDE, load_prompt_sync
    from services.gateway.tool_registry import hub_tool_guide

    if surface not in ("single_turn", "raven"):
        raise HTTPException(status_code=422, detail="surface is single_turn or raven")
    guide = hub_tool_guide(load_prompt_sync(PROMPT_SINGLE_TURN_TOOL_GUIDE)) if surface == "single_turn" else ""
    text = knowledge_map_for(surface, is_admin=admin, guide=guide)
    return {"surface": surface, "admin": admin, "chars": len(text), "map": text}


class InvalidateRequest(BaseModel):
    tag: str | None = None
    user: str | None = None


class TuneRequest(BaseModel):
    apply: bool = False


@router.get("/api/admin/tool-cache")
async def get_tool_cache(request: Request):
    """Declared freshness, applied tuning, suggestions, and hit rate and time saved per tool."""
    await _admin(request)
    from services.gateway import tool_cache

    return tool_cache.report()


@router.post("/api/admin/tool-cache/invalidate")
async def invalidate_tool_cache(body: InvalidateRequest, request: Request):
    """Drop cached results by tag (``files``, ``lessons``, ``devices``...) or by user.

    Also how another service reports an event that makes data stale (an index rebuild).
    """
    await _admin(request)
    from fastapi import HTTPException

    from services.gateway import tool_cache

    if bool(body.tag) == bool(body.user):
        raise HTTPException(status_code=422, detail="Name exactly one of tag or user.")
    dropped = tool_cache.invalidate_tag(body.tag, reason="admin") if body.tag else tool_cache.invalidate_user(body.user or "")
    return {"status": "SUCCESS", "dropped": dropped}


@router.post("/api/admin/tool-cache/tune")
async def tune_tool_cache(body: TuneRequest, request: Request):
    """The tuning suggestions now; ``apply`` makes them (live tools are never changed)."""
    await _admin(request)
    from services.gateway import tool_cache

    return {"suggestions": tool_cache.tune(apply=body.apply)}


@router.delete("/api/admin/tool-cache/overrides/{tool}")
async def clear_tool_cache_override(tool: str, request: Request):
    """Put a tool back on its declared freshness."""
    await _admin(request)
    from services.gateway import tool_cache

    tool_cache.clear_override(tool)
    return {"status": "SUCCESS"}
