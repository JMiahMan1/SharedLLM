# services/execution/abs_client.py
"""
Audiobookshelf (ABS) REST API client.
Handles authentication, library search, playback progress, and streaming.
"""
import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger("execution.abs_client")

_TIMEOUT = aiohttp.ClientTimeout(total=30.0, connect=5.0)

# Per-ABS-server caches (in-memory, TTL-based) to avoid re-authenticating and
# re-discovering the book library on every request. Keys are by abs_url so this
# stays correct across multiple users sharing one ABS server.
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 1800  # 30 minutes


def _cache_get(abs_url: str, field: str) -> Any | None:
    entry = _CACHE.get(abs_url.rstrip("/"))
    if not entry or field not in entry:
        return None
    value, exp = entry[field]
    if exp > time.time():
        return value
    return None


def _cache_set(abs_url: str, field: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    _CACHE.setdefault(abs_url.rstrip("/"), {})
    _CACHE[abs_url.rstrip("/")][field] = (value, time.time() + ttl)


def resolve_abs_credentials(user_context: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract ABS URL and auth from user context.
    Returns (abs_url, abs_api_key, username, password).
    User context comes from ResolvedCredentials with fields:
      audiobookshelf_url, audiobookshelf_user, audiobookshelf_pass
    """
    def get_val(obj, key, alt_key=None):
        if not obj:
            return None
        if isinstance(obj, dict):
            return obj.get(key) or (obj.get(alt_key) if alt_key else None)
        return getattr(obj, key, None) or (getattr(obj, alt_key, None) if alt_key else None)

    abs_url = get_val(user_context, "audiobookshelf_url", "abs_url")
    abs_key = get_val(user_context, "abs_api_key", "audiobookshelf_api_key")
    username = get_val(user_context, "audiobookshelf_user", "abs_username")
    password = get_val(user_context, "audiobookshelf_pass", "abs_password")

    return abs_url, abs_key, username, password


async def abs_login(abs_url: str, username: str, password: str, force: bool = False) -> str | None:
    """Login to ABS with username/password and return API token.

    Caches the token per ABS server (TTL-based) so repeated searches do not
    re-authenticate on every request. Falls back to a cached (possibly stale)
    token if the live login fails.
    """
    cache_key = f"token:{username}"
    if not force:
        cached = _cache_get(abs_url, cache_key)
        if cached:
            return cached
    url = f"{abs_url.rstrip('/')}/login"
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as client:
        try:
            async with client.post(url, json={"username": username, "password": password}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                token = data.get("user", {}).get("token")
                if token:
                    _cache_set(abs_url, cache_key, token)
                return token
        except Exception as e:
            log.error(f"[abs_client] Login failed: {e}")
            # Fall back to a cached token if the live login failed
            cached = _cache_get(abs_url, cache_key)
            return cached


async def abs_get(
    abs_url: str, abs_api_key: str, path: str, params: dict | None = None
) -> dict:
    """GET request to ABS API."""
    url = f"{abs_url.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {abs_api_key}"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as client:
        try:
            async with client.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            log.error(f"[abs_client] HTTP error on GET {path}: {e}")
            return {"error": f"ABS returned {e.status}: {e.message}"}
        except Exception as e:
            log.error(f"[abs_client] GET {path} failed: {e}")
            return {"error": f"Audiobookshelf is unreachable: {e}"}


async def abs_post(
    abs_url: str, abs_api_key: str, path: str, json: dict | None = None
) -> dict:
    """POST request to ABS API."""
    url = f"{abs_url.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {abs_api_key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as client:
        try:
            async with client.post(url, headers=headers, json=json) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data
        except aiohttp.ClientResponseError as e:
            log.error(f"[abs_client] HTTP error on POST {path}: {e}")
            return {"error": f"ABS returned {e.status}: {e.message}"}
        except Exception as e:
            log.error(f"[abs_client] POST {path} failed: {e}")
            return {"error": f"Audiobookshelf is unreachable: {e}"}


async def search_library(
    abs_url: str, abs_api_key: str, query: str, limit: int = 10
) -> dict:
    """Search the ABS book library for audiobooks matching the query.

    Gets the book library ID and searches within it using the correct ABS API endpoint.
    The book library ID is cached per ABS server to avoid re-listing libraries on
    every search.
    """
    # Resolve (and cache) the book library ID
    book_lib_id = _cache_get(abs_url, "book_lib_id")
    if not book_lib_id:
        libs = await abs_get(abs_url, abs_api_key, "/api/libraries")
        if "error" in libs:
            return libs
        for lib in libs.get("libraries", []):
            if lib.get("mediaType") == "book":
                book_lib_id = lib.get("id")
                break
        if not book_lib_id:
            return {"error": "No book library found"}
        _cache_set(abs_url, "book_lib_id", book_lib_id)

    # Search within the book library
    return await abs_get(abs_url, abs_api_key, f"/api/libraries/{book_lib_id}/items", params={"query": query, "limit": limit})


async def search_books(
    abs_url: str, abs_api_key: str, title: str = "", author: str = "", provider: str = "google"
) -> dict:
    """Search external metadata providers for books (iTunes, Audible, Google, OpenLibrary)."""
    params = {"provider": provider}
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    return await abs_get(abs_url, abs_api_key, "/api/search/books", params=params)


async def search_podcasts(
    abs_url: str, abs_api_key: str, term: str
) -> dict:
    """Search iTunes for podcasts."""
    return await abs_get(abs_url, abs_api_key, "/api/search/podcast", params={"term": term})


async def search_authors(
    abs_url: str, abs_api_key: str, query: str
) -> dict:
    """Search Audnexus/Audible for authors."""
    return await abs_get(abs_url, abs_api_key, "/api/search/authors", params={"q": query})


async def search_all(
    abs_url: str, abs_api_key: str, query: str, limit: int = 30
) -> dict:
    """Generic search: books + podcasts + authors from external metadata providers."""
    import asyncio

    results = {"books": [], "podcasts": [], "authors": []}

    async def _gather():
        tasks = [
            search_books(abs_url, abs_api_key, title=query),
            search_podcasts(abs_url, abs_api_key, term=query),
            search_authors(abs_url, abs_api_key, query=query),
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    book_res, podcast_res, author_res = await _gather()

    for r in [book_res, podcast_res, author_res]:
        if isinstance(r, Exception):
            log.warning(f"[abs_client] Generic search sub-error: {r}")

    if isinstance(book_res, dict) and "error" not in book_res:
        books = book_res if isinstance(book_res, list) else book_res.get("results", book_res.get("books", []))
        results["books"] = books[:limit]

    if isinstance(podcast_res, dict) and "error" not in podcast_res:
        pods = podcast_res if isinstance(podcast_res, list) else podcast_res.get("results", podcast_res.get("podcasts", []))
        results["podcasts"] = pods[:limit]

    if isinstance(author_res, dict) and "error" not in author_res:
        author_list = author_res if isinstance(author_res, list) else [author_res]
        if "authors" in author_res:
            author_list = author_res["authors"]
        results["authors"] = [a for a in author_list if a] if isinstance(author_list, list) else []

    return results


async def get_library_items(
    abs_url: str, abs_api_key: str, library_id: str = "", limit: int = 25, page: int = 0
) -> dict:
    """Get items from a specific ABS library."""
    path = f"/api/v1/libraries/{library_id}/items" if library_id else "/api/v1/libraries/items"
    return await abs_get(abs_url, abs_api_key, path, params={"limit": limit, "page": page})


async def get_book(abs_url: str, abs_api_key: str, book_id: str) -> dict:
    """Get full details for a specific audiobook."""
    return await abs_get(abs_url, abs_api_key, f"/api/v1/items/{book_id}")


async def get_progress(abs_url: str, abs_api_key: str, user_id: str = "me") -> dict:
    """Get the user's playback progress for all books."""
    return await abs_get(abs_url, abs_api_key, f"/api/v1/users/{user_id}/progress")


async def get_book_progress(
    abs_url: str, abs_api_key: str, item_id: str, user_id: str = "me"
) -> dict:
    """Get playback progress for a specific book."""
    return await abs_get(abs_url, abs_api_key, f"/api/v1/users/{user_id}/progress/{item_id}")


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
    return await abs_get(abs_url, abs_api_key, "/api/libraries")


async def authorize_token(abs_url: str, abs_api_key: str) -> dict:
    """Validate API token and get user/server info."""
    return await abs_get(abs_url, abs_api_key, "/authorize")


async def get_items_in_progress(abs_url: str, abs_api_key: str) -> dict:
    """Get user's in-progress items (better than /me/progress for continue listening)."""
    return await abs_get(abs_url, abs_api_key, "/api/me/items-in-progress")


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
    current_time: float | None = None, duration: float | None = None
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
