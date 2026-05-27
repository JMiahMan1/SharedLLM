# services/execution/abs_client.py
"""
Audiobookshelf (ABS) REST API client.
Handles authentication, library search, playback progress, and streaming.
"""
import logging
import os
import httpx
from typing import Optional, Any

log = logging.getLogger("execution.abs_client")

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def resolve_abs_credentials(user_context: Any) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract ABS URL and auth from user context or environment fallback.
    Returns (abs_url, abs_api_key, username, password).
    User context comes from ResolvedCredentials with fields:
      audiobookshelf_url, audiobookshelf_user, audiobookshelf_pass
    """
    abs_url = (
        getattr(user_context, "audiobookshelf_url", None)
        or getattr(user_context, "abs_url", None)
        or os.getenv("AUDIOBOOKSHELF_URL")
        or os.getenv("ABS_URL")
    )
    abs_key = (
        getattr(user_context, "abs_api_key", None)
        or os.getenv("ABS_API_KEY")
    )
    username = (
        getattr(user_context, "audiobookshelf_user", None)
        or getattr(user_context, "abs_username", None)
        or os.getenv("AUDIOBOOKSHELF_USER")
        or os.getenv("ABS_USER")
    )
    password = (
        getattr(user_context, "audiobookshelf_pass", None)
        or getattr(user_context, "abs_password", None)
        or os.getenv("AUDIOBOOKSHELF_PASS")
        or os.getenv("ABS_PASS")
    )
    return abs_url, abs_key, username, password


async def abs_login(abs_url: str, username: str, password: str) -> Optional[str]:
    """Login to ABS with username/password and return API token."""
    url = f"{abs_url.rstrip('/')}/api/login"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json={"username": username, "password": password})
            resp.raise_for_status()
            data = resp.json()
            return data.get("user", {}).get("token")
        except Exception as e:
            log.error(f"[abs_client] Login failed: {e}")
            return None


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
    return await abs_get(abs_url, abs_api_key, "/me/progress")


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


async def authorize_token(abs_url: str, abs_api_key: str) -> dict:
    """Validate API token and get user/server info."""
    return await abs_get(abs_url, abs_api_key, "/authorize")


async def get_items_in_progress(abs_url: str, abs_api_key: str) -> dict:
    """Get user's in-progress items (better than /me/progress for continue listening)."""
    return await abs_get(abs_url, abs_api_key, "/me/items-in-progress")


async def get_listening_sessions(abs_url: str, abs_api_key: str, limit: int = 10) -> dict:
    """Get user's recent listening sessions."""
    return await abs_get(abs_url, abs_api_key, "/me/listening-sessions", params={"limit": limit})


async def sync_local_session(
    abs_url: str, abs_api_key: str, session_data: dict
) -> dict:
    """Sync a local (offline) playback session to the server.
    session_data should be a full PlaybackSession object with UUIDv4 id.
    """
    return await abs_post(abs_url, abs_api_key, "/session/local", json=session_data)


async def sync_session_position(
    abs_url: str, abs_api_key: str, session_id: str,
    current_time: float, time_listened: float, duration: float
) -> dict:
    """Sync position during active playback."""
    return await abs_post(
        abs_url, abs_api_key, f"/session/{session_id}/sync",
        json={"currentTime": current_time, "timeListened": time_listened, "duration": duration}
    )


async def close_session(
    abs_url: str, abs_api_key: str, session_id: str,
    current_time: Optional[float] = None, duration: Optional[float] = None
) -> dict:
    """Close a playback session, optionally with final position."""
    payload = {}
    if current_time is not None:
        payload["currentTime"] = current_time
    if duration is not None:
        payload["duration"] = duration
    return await abs_post(abs_url, abs_api_key, f"/session/{session_id}/close", json=payload if payload else None)


async def batch_update_progress(
    abs_url: str, abs_api_key: str, progress_updates: list[dict]
) -> dict:
    """Batch update progress for multiple items.
    Each update: {"libraryItemId": str, "currentTime": float, "duration": float, "isComplete": bool}
    """
    return await abs_post(
        abs_url, abs_api_key, "/me/progress/batch/update",
        json={"progress": progress_updates}
    )


async def get_library_collections(abs_url: str, abs_api_key: str, library_id: str) -> dict:
    """Get collections in a library."""
    return await abs_get(abs_url, abs_api_key, f"/libraries/{library_id}/collections")


async def get_library_series(abs_url: str, abs_api_key: str, library_id: str) -> dict:
    """Get series in a library."""
    return await abs_get(abs_url, abs_api_key, f"/libraries/{library_id}/series")


async def get_user_playlists(abs_url: str, abs_api_key: str, library_id: str) -> dict:
    """Get user's playlists for a library."""
    return await abs_get(abs_url, abs_api_key, f"/libraries/{library_id}/playlists")


async def get_podcast_episode(
    abs_url: str, abs_api_key: str, item_id: str, episode_id: str
) -> dict:
    """Get details for a specific podcast episode."""
    return await abs_get(abs_url, abs_api_key, f"/items/{item_id}/episodes/{episode_id}")
