# services/execution/handlers/audiobookshelf.py
"""
Audiobookshelf (ABS) handler — search, play, resume, and track audiobook progress.
Integrates with Home Assistant media_player for playback on any supported device.
"""
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import ha_client
    import abs_client
    from schemas import AudiobookshelfRequest, ExecutionResult
except ImportError:
    import ha_client
    import abs_client
    from schemas import AudiobookshelfRequest, ExecutionResult

log = logging.getLogger("execution.audiobookshelf")


async def handle_audiobookshelf(req: AudiobookshelfRequest) -> ExecutionResult:
    ctx = req.user_context
    abs_url, abs_key, username, password = abs_client.resolve_abs_credentials(ctx)
    if not abs_url:
        return ExecutionResult(
            status="FAILURE",
            message="Audiobookshelf URL not configured.",
            service="audiobookshelf",
        )
    if not abs_key and (username and password):
        abs_key = await abs_client.abs_login(abs_url, username, password)
        if not abs_key:
            return ExecutionResult(
                status="FAILURE",
                message="Audiobookshelf login failed with provided credentials.",
                service="audiobookshelf",
            )
    if not abs_key:
        return ExecutionResult(
            status="FAILURE",
            message="Audiobookshelf API key or username/password not configured.",
            service="audiobookshelf",
        )

    action = req.action
    log.info(f"[abs] action={action} query={req.query} book_id={req.book_id}")

    try:
        if action == "search":
            return await _handle_search(abs_url, abs_key, req)

        elif action == "play":
            return await _handle_play(abs_url, abs_key, req)

        elif action == "resume":
            return await _handle_resume(abs_url, abs_key, req)

        elif action == "progress":
            return await _handle_progress(abs_url, abs_key, req)

        elif action == "libraries":
            return await _handle_libraries(abs_url, abs_key)

        elif action == "list":
            return await _handle_list(abs_url, abs_key, req)

        elif action == "get_book":
            return await _handle_get_book(abs_url, abs_key, req)

        return ExecutionResult(
            status="FAILURE",
            message=f"Action '{action}' not supported.",
            service="audiobookshelf",
        )

    except Exception as e:
        log.error(f"[abs] Error: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Audiobookshelf error: {e}",
            service="audiobookshelf",
        )


async def _handle_search(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.query:
        return ExecutionResult(status="FAILURE", message="Search query is required.", service="audiobookshelf")

    result = await abs_client.search_library(abs_url, abs_key, req.query, limit=req.limit)
    if "error" in result:
        return ExecutionResult(status="FAILURE", message=result["error"], service="audiobookshelf")

    books = result.get("book", [])
    if not books:
        return ExecutionResult(status="SUCCESS", message=f"No audiobooks found for '{req.query}'.", service="audiobookshelf")

    summaries = []
    for b in books[:req.limit]:
        book = b.get("libraryItem", {})
        meta = book.get("media", {}).get("metadata", {})
        summaries.append({
            "id": book.get("id"),
            "title": meta.get("title", "Unknown"),
            "author": meta.get("authorName", "Unknown"),
            "narrator": meta.get("narratorName", "Unknown"),
            "duration": book.get("media", {}).get("duration", 0),
        })

    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {len(summaries)} audiobook(s) for '{req.query}'.",
        service="audiobookshelf",
        detail={"books": summaries},
    )


async def _handle_play(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required to play.", service="audiobookshelf")

    if req.book_id:
        book_id = req.book_id
        book_title = req.book_id
    elif req.query:
        search = await abs_client.search_library(abs_url, abs_key, req.query, limit=1)
        if "error" in search or not search.get("book"):
            return ExecutionResult(status="FAILURE", message=f"No audiobook found for '{req.query}'.", service="audiobookshelf")
        book = search["book"][0].get("libraryItem", {})
        book_id = book.get("id")
        book_title = book.get("media", {}).get("metadata", {}).get("title", req.query)
    else:
        return ExecutionResult(status="FAILURE", message="book_id or query is required.", service="audiobookshelf")

    stream_url = await abs_client.get_stream_url(abs_url, abs_key, book_id)
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)

    state = await ha_client.get_state(ctx_ha_url(req), ctx_ha_token(req), full_entity_id)
    if state and state.get("state") == "off":
        await ha_client.call_service(ctx_ha_url(req), ctx_ha_token(req), "media_player", "turn_on", full_entity_id)
        import asyncio
        await asyncio.sleep(2)

    result = await ha_client.call_service(
        ctx_ha_url(req), ctx_ha_token(req),
        "media_player", "play_media",
        full_entity_id,
        {"media_content_id": stream_url, "media_content_type": "audio/mp4"},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Now playing: {book_title}", service="audiobookshelf")
    return ExecutionResult(status="FAILURE", message=f"Playback failed: {result.get('error')}", service="audiobookshelf")


async def _handle_resume(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required to resume.", service="audiobookshelf")

    progress = await abs_client.get_progress(abs_url, abs_key)
    if "error" in progress:
        return ExecutionResult(status="FAILURE", message=progress["error"], service="audiobookshelf")

    items = progress.get("mediaProgress", [])
    in_progress = [i for i in items if not i.get("isComplete") and i.get("currentTime", 0) > 0]
    if not in_progress:
        return ExecutionResult(status="SUCCESS", message="No audiobooks in progress.", service="audiobookshelf")

    latest = sorted(in_progress, key=lambda x: x.get("lastUpdate", 0), reverse=True)[0]
    item_id = latest.get("itemId") or latest.get("libraryItemId")
    duration = latest.get("duration", 0)
    current = latest.get("currentTime", 0)
    pct = int((current / duration) * 100) if duration else 0

    book_detail = await abs_client.get_book(abs_url, abs_key, item_id)
    title = book_detail.get("media", {}).get("metadata", {}).get("title", "Unknown")

    stream_url = await abs_client.get_stream_url(abs_url, abs_key, item_id)
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)

    result = await ha_client.call_service(
        ctx_ha_url(req), ctx_ha_token(req),
        "media_player", "play_media",
        full_entity_id,
        {"media_content_id": stream_url, "media_content_type": "audio/mp4"},
    )

    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Resuming '{title}' at {pct}% ({_format_time(current)} / {_format_time(duration)})",
            service="audiobookshelf",
        )
    return ExecutionResult(status="FAILURE", message=f"Resume failed: {result.get('error')}", service="audiobookshelf")


async def _handle_progress(abs_url: str, abs_key: str, req) -> ExecutionResult:
    progress = await abs_client.get_progress(abs_url, abs_key)
    if "error" in progress:
        return ExecutionResult(status="FAILURE", message=progress["error"], service="audiobookshelf")

    items = progress.get("mediaProgress", [])
    in_progress = [i for i in items if not i.get("isComplete") and i.get("currentTime", 0) > 0]
    if not in_progress:
        return ExecutionResult(status="SUCCESS", message="No audiobooks currently in progress.", service="audiobookshelf")

    summaries = []
    for i in sorted(in_progress, key=lambda x: x.get("lastUpdate", 0), reverse=True)[:10]:
        item_id = i.get("itemId") or i.get("libraryItemId")
        book = await abs_client.get_book(abs_url, abs_key, item_id)
        meta = book.get("media", {}).get("metadata", {})
        duration = i.get("duration", 0)
        current = i.get("currentTime", 0)
        pct = int((current / duration) * 100) if duration else 0
        summaries.append({
            "title": meta.get("title", "Unknown"),
            "author": meta.get("authorName", ""),
            "progress": f"{pct}%",
            "time": f"{_format_time(current)} / {_format_time(duration)}",
        })

    return ExecutionResult(
        status="SUCCESS",
        message=f"You have {len(summaries)} audiobook(s) in progress.",
        service="audiobookshelf",
        detail={"in_progress": summaries},
    )


async def _handle_libraries(abs_url: str, abs_key: str) -> ExecutionResult:
    result = await abs_client.get_libraries(abs_url, abs_key)
    if "error" in result:
        return ExecutionResult(status="FAILURE", message=result["error"], service="audiobookshelf")

    libs = result.get("libraries", [])
    summaries = [{"id": lib["id"], "name": lib["name"], "type": lib.get("mediaType")} for lib in libs]
    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {len(summaries)} library/libraries.",
        service="audiobookshelf",
        detail={"libraries": summaries},
    )


async def _handle_list(abs_url: str, abs_key: str, req) -> ExecutionResult:
    result = await abs_client.get_library_items(abs_url, abs_key, req.library_id or "", limit=req.limit)
    if "error" in result:
        return ExecutionResult(status="FAILURE", message=result["error"], service="audiobookshelf")

    items = result.get("results", [])
    summaries = []
    for item in items[:req.limit]:
        meta = item.get("media", {}).get("metadata", {})
        summaries.append({
            "id": item.get("id"),
            "title": meta.get("title", "Unknown"),
            "author": meta.get("authorName", "Unknown"),
        })

    return ExecutionResult(
        status="SUCCESS",
        message=f"Listed {len(summaries)} audiobook(s).",
        service="audiobookshelf",
        detail={"books": summaries},
    )


async def _handle_get_book(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.book_id:
        return ExecutionResult(status="FAILURE", message="book_id is required.", service="audiobookshelf")

    book = await abs_client.get_book(abs_url, abs_key, req.book_id)
    if "error" in book:
        return ExecutionResult(status="FAILURE", message=book["error"], service="audiobookshelf")

    meta = book.get("media", {}).get("metadata", {})
    return ExecutionResult(
        status="SUCCESS",
        message=f"Retrieved details for '{meta.get('title', 'Unknown')}'.",
        service="audiobookshelf",
        detail={
            "title": meta.get("title"),
            "author": meta.get("authorName"),
            "narrator": meta.get("narratorName"),
            "series": meta.get("seriesName"),
            "genres": meta.get("genres", []),
            "description": meta.get("description", ""),
            "duration": book.get("media", {}).get("duration", 0),
            "chapters": len(book.get("media", {}).get("chapters", [])),
        },
    )


def _format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s"


def ctx_ha_url(req) -> str:
    return getattr(req.user_context, "ha_url", "")


def ctx_ha_token(req) -> str:
    return getattr(req.user_context, "ha_token", "")
