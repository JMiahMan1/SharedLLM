"""Image entities (HA's ``image`` domain, such as a robot vacuum's map) as pictures for the app.

The hub serves an image entity at ``/api/image_proxy/{entity_id}`` (homehub ``api.py``). This route
fetches it with the caller's own hub credentials, exactly as the camera snapshot route does
(``camera_routes``), so the HA token never reaches the browser. An ``<img>`` cannot send a header,
so the picture takes the signed ``?mt=&user=`` media token. An image the caller may not see (the
device permissions every HA surface uses) answers 404 as if it did not exist.
"""
from __future__ import annotations

import logging
import re

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from services.gateway import camera_routes

log = logging.getLogger("gateway.images")

router = APIRouter()

#: Entity ids go into the hub's URL path, so only the plain HA shape passes.
_IMAGE_ID = re.compile(r"^image\.[a-z0-9_]+$")
#: A vacuum renders its map on request; a few seconds is normal.
_IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)
_NOT_FOUND = "Image not found"


@router.get("/api/media/images/{entity_id}")
async def image(entity_id: str, request: Request):
    """The current picture of an image entity."""
    from services.gateway.main import shared_http_client

    if not _IMAGE_ID.match(entity_id or ""):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    creds = await camera_routes._caller(request, media=True)
    if not camera_routes.can_view(await camera_routes._permissions(creds), entity_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    base, headers = camera_routes._hub(creds)
    try:
        async with shared_http_client() as client, client.get(
            f"{base}/api/image_proxy/{entity_id}", headers=headers, timeout=_IMAGE_TIMEOUT
        ) as resp:
            status = resp.status
            body = await resp.read() if status == 200 else b""
            ctype = resp.headers.get("Content-Type", "") if status == 200 else ""
            message = "" if status == 200 else await camera_routes._hub_message(resp)
    except (TimeoutError, aiohttp.ClientError) as exc:
        raise HTTPException(status_code=503, detail="The image is not available") from exc
    if status == 404:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if status != 200 or not body or not ctype.startswith("image/"):
        if status in (401, 403):
            log.warning("[images] %s: hub answered HTTP %s", entity_id, status)
        # The hub's own words ("switch it to Upstairs to see this one") are for people.
        raise HTTPException(status_code=503, detail=message or "The image is not available")
    return Response(content=body, media_type=ctype, headers={"Cache-Control": "private, no-store"})
