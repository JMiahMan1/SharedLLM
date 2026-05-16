# services/execution/abs_client.py
"""
Audiobookshelf (ABS) REST API client.
Handles authentication, library search, playback progress, and streaming.
"""
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from typing import Optional, Any

log = logging.getLogger("execution.abs_client")

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def resolve_abs_credentials(user_context: Any) -> tuple[Optional[str], Optional[str]]:
    """Extract ABS URL and API key from user context or environment fallback."""
    abs_url = getattr(user_context, "abs_url", None) or os.getenv("ABS_URL")
    abs_key = getattr(user_context, "abs_api_key", None) or os.getenv("ABS_API_KEY")
    return abs_url, abs_key


async def abs_get(
    abs_url: str, abs_api_key: str, path: str, params: Optional[dict] = None
) -> dict:
    """GET request to ABS API."""
    url = f"{abs_url.rstrip('/')}/api{path}"
    headers = {"Authorization": abs_api_key}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            log.error(f"[abs_client] HTTP error on GET {path}: {e}")
            return {"error": f"ABS returned {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            log.error(f"[abs_client] GET {path} failed: {e}")
            return {"error": f"Audiobookshelf is unreachable: {e}"}


async def abs_post(
    abs_url: str, abs_api_key: str, path: str, json: Optional[dict] = None
) -> dict:
    """POST request to ABS API."""
    url = f"{abs_url.rstrip('/')}/api{path}"
    headers = {"Authorization": abs_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, headers=headers, json=json)
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except httpx.HTTPStatusError as e:
            log.error(f"[abs_client] HTTP error on POST {path}: {e}")
            return {"error": f"ABS returned {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            log.error(f"[abs_client] POST {path} failed: {e}")
            return {"error": f"Audiobookshelf is unreachable: {e}"}


async def search_library(
    abs_url: str, abs_api_key: str, query: str, limit: int = 10
) -> dict:
    """Search the ABS library for audiobooks matching the query."""
    return await abs_get(abs_url, abs_api_key, "/search", params={"q": query, "limit": limit})


async def get_library_items(
    abs_url: str, abs_api_key: str, library_id: str = "", limit: int = 25, page: int = 0
) -> dict:
    """Get items from a specific ABS library."""
    path = f"/libraries/{library_id}/items" if library_id else "/libraries/items"
    return await abs_get(abs_url, abs_api_key, path, params={"limit": limit, "page": page})


async def get_book(abs_url: str, abs_api_key: str, book_id: str) -> dict:
    """Get full details for a specific audiobook."""
    return await abs_get(abs_url, abs_api_key, f"/items/{book_id}")


async def get_progress(abs_url: str, abs_api_key: str, user_id: str = "me") -> dict:
    """Get the user's playback progress for all books."""
    return await abs_get(abs_url, abs_api_key, f"/me/progress")


async def get_book_progress(
    abs_url: str, abs_api_key: str, item_id: str, user_id: str = "me"
) -> dict:
    """Get playback progress for a specific book."""
    return await abs_get(abs_url, abs_api_key, f"/me/progress/{item_id}")


async def update_progress(
    abs_url: str,
    abs_api_key: str,
    item_id: str,
    current_time: float,
    duration: float,
    is_complete: bool = False,
) -> dict:
    """Update playback progress for a book."""
    return await abs_post(
        abs_url,
        abs_api_key,
        f"/me/progress/{item_id}",
        json={
            "currentTime": current_time,
            "duration": duration,
            "isComplete": is_complete,
        },
    )


async def get_stream_url(
    abs_url: str, abs_api_key: str, item_id: str, format: str = "mp4"
) -> str:
    """Get the direct stream URL for an audiobook."""
    return f"{abs_url.rstrip('/')}/api/items/{item_id}/stream?format={format}&token={abs_api_key}"


async def get_libraries(abs_url: str, abs_api_key: str) -> dict:
    """List all ABS libraries."""
    return await abs_get(abs_url, abs_api_key, "/libraries")
