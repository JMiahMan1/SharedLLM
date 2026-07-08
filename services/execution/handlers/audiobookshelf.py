# services/execution/handlers/audiobookshelf.py
"""
Audiobookshelf (ABS) handler — search, play, resume, and track audiobook progress.
Integrates with Home Assistant media_player for playback on any supported device.
"""
import logging
from datetime import datetime

from services.execution import abs_client, ha_client
from services.execution.schemas import AudiobookshelfRequest, ExecutionResult

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
    if username and password:
        # Always obtain a fresh token via login; the stored API key may be stale/expired.
        fresh = await abs_client.abs_login(abs_url, username, password)
        if fresh:
            abs_key = fresh
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

        elif action == "last_played":
            return await _handle_last_played(abs_url, abs_key)

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

    # Search ABS library
    library_result = await abs_client.search_library(abs_url, abs_key, req.query, limit=req.limit)
    if "error" in library_result:
        log.warning(f"[abs] Library search failed, falling back to external: {library_result.get('error')}")
        # Fall through to external metadata search

    books = library_result.get("book", [])
    if books:
        book_summaries = []
        for b in books[:req.limit]:
            library_item = b.get("libraryItem", {})
            media = library_item.get("media", {})
            meta = media.get("metadata", {})
            book_id = library_item.get("id", "")
            chapters = media.get("chapters", [])
            book_summaries.append({
                "id": book_id,
                "title": meta.get("title", "Unknown"),
                "author": meta.get("authorName", "Unknown"),
                "narrator": meta.get("narratorName", ""),
                "series": meta.get("series", ""),
                "publishedYear": meta.get("publishedYear", ""),
                "genres": meta.get("genres", []),
                "duration": media.get("duration", 0),
                "duration_formatted": _format_time(media.get("duration", 0)) if media.get("duration") else "",
                "progress": library_item.get("progress", {}),
                "status": library_item.get("status", ""),
                "chapter_count": len(chapters) if isinstance(chapters, list) else 0,
                "play_url": await abs_client.get_stream_url(abs_url, abs_key, book_id) if book_id else "",
                "cover": media.get("cover", {}).get("path", "") if isinstance(media.get("cover"), dict) else (media.get("cover", "") or ""),
            })

        return ExecutionResult(
            status="SUCCESS",
            message=f"Found {len(book_summaries)} audiobook(s) in library for '{req.query}'.",
            service="audiobookshelf",
            detail={"books": book_summaries},
        )

    # Fallback: external metadata search
    result = await abs_client.search_all(abs_url, abs_key, req.query, limit=req.limit)

    book_summaries = []
    for b in result.get("books", [])[:req.limit]:
        if isinstance(b, dict):
            book_summaries.append({
                "id": b.get("id", b.get("key", "")),
                "title": b.get("title", "Unknown"),
                "author": b.get("author", b.get("authorName", "")),
                "narrator": b.get("narrator", b.get("narratorName", "")),
                "publisher": b.get("publisher", ""),
                "description": b.get("description", ""),
                "cover": b.get("cover", b.get("coverUrl", "")),
                "genres": b.get("genres", []),
                "publishedYear": b.get("publishedYear", ""),
                "asin": b.get("asin", ""),
                "type": "book",
                "source": "external",
            })

    podcast_summaries = []
    for p in result.get("podcasts", [])[:req.limit]:
        if isinstance(p, dict):
            podcast_summaries.append({
                "id": p.get("id", ""),
                "title": p.get("title", p.get("collectionName", "Unknown")),
                "author": p.get("artistName", p.get("publisher", "")),
                "description": p.get("descriptionPlain", p.get("description", "")),
                "cover": p.get("cover", p.get("artworkUrl", "")),
                "trackCount": p.get("trackCount", 0),
                "genres": p.get("genres", []),
                "feedUrl": p.get("feedUrl", p.get("podcastUrl", "")),
                "explicit": p.get("explicit", False),
                "type": "podcast",
                "source": "itunes",
            })

    author_summaries = []
    for a in result.get("authors", [])[:req.limit]:
        if isinstance(a, dict):
            author_summaries.append({
                "id": a.get("id", ""),
                "name": a.get("name", "Unknown"),
                "description": a.get("description", ""),
                "image": a.get("image", a.get("imageUrl", "")),
                "asin": a.get("asin", ""),
                "type": "author",
                "source": "audnexus",
            })

    total = len(book_summaries) + len(podcast_summaries) + len(author_summaries)
    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {total} result(s) for '{req.query}'.",
        service="audiobookshelf",
        detail={
            "books": book_summaries,
            "podcasts": podcast_summaries,
            "authors": author_summaries,
            "total": total,
        },
    )


async def _handle_play(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required to play.", service="audiobookshelf")

    if req.book_id:
        book_id = req.book_id
        book_title = req.book_id
    elif req.query:
        search = await abs_client.search_library(abs_url, abs_key, req.query, limit=1)
        if "error" in search or not search.get("results"):
            return ExecutionResult(status="FAILURE", message=f"No audiobook found for '{req.query}'.", service="audiobookshelf")
        book = search["results"][0]
        book_id = book.get("id")
        book_title = book.get("media", {}).get("metadata", {}).get("title", req.query)
    else:
        return ExecutionResult(status="FAILURE", message="book_id or query is required.", service="audiobookshelf")

    stream_url = await abs_client.get_stream_url(abs_url, abs_key, book_id)
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    ha_url = ctx_ha_url(req)
    ha_token = ctx_ha_token(req)

    state = await ha_client.get_state(ha_url, ha_token, full_entity_id)
    if state and state.get("state") == "off":
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", full_entity_id)
        import asyncio
        await asyncio.sleep(2)

    # Detect if this is a Roku device — needs two-step MASS flow
    from . import roku as roku_handler
    is_roku = await roku_handler.is_roku_device(ha_url, ha_token, full_entity_id)

    if is_roku:
        return await _roku_play_audiobook(full_entity_id, stream_url, book_title, ha_url, ha_token)

    # Detect if this is a Music Assistant player
    is_ma = False
    if state:
        attrs = state.get("attributes", {})
        is_ma = (
            attrs.get("integration") == "music_assistant"
            or "music assistant" in attrs.get("source", "").lower()
            or attrs.get("active_queue") is not None
            or full_entity_id.startswith("media_player.mass_")
        )

    if is_ma:
        log.info(f"[abs] Playing on MA player '{full_entity_id}' using music_assistant.play_media")
        result = await ha_client.call_service(
            ha_url, ha_token,
            "music_assistant", "play_media",
            full_entity_id,
            {"media_id": stream_url, "media_type": "track", "enqueue": "play"},
        )
    else:
        # All other devices: direct play_media with ABS stream URL
        result = await ha_client.call_service(
            ha_url, ha_token,
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

    progress = await abs_client.get_items_in_progress(abs_url, abs_key)
    if "error" in progress:
        return ExecutionResult(status="FAILURE", message=progress["error"], service="audiobookshelf")

    items = progress.get("libraryItems", [])
    if not items:
        return ExecutionResult(status="SUCCESS", message="No audiobooks in progress.", service="audiobookshelf")

    latest = sorted(items, key=lambda x: x.get("progressLastUpdate", 0), reverse=True)[0]
    item_id = latest.get("id", "")

    title = latest.get("media", {}).get("metadata", {}).get("title", "Unknown")

    stream_url = await abs_client.get_stream_url(abs_url, abs_key, item_id)
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    ha_url = ctx_ha_url(req)
    ha_token = ctx_ha_token(req)

    # Detect if this is a Roku device
    from . import roku as roku_handler
    is_roku = await roku_handler.is_roku_device(ha_url, ha_token, full_entity_id)

    if is_roku:
        return await _roku_play_audiobook(full_entity_id, stream_url, title, ha_url, ha_token)

    state = await ha_client.get_state(ha_url, ha_token, full_entity_id)
    # Detect if this is a Music Assistant player
    is_ma = False
    if state:
        attrs = state.get("attributes", {})
        is_ma = (
            attrs.get("integration") == "music_assistant"
            or "music assistant" in attrs.get("source", "").lower()
            or attrs.get("active_queue") is not None
            or full_entity_id.startswith("media_player.mass_")
        )

    if is_ma:
        log.info(f"[abs] Resuming on MA player '{full_entity_id}' using music_assistant.play_media")
        result = await ha_client.call_service(
            ha_url, ha_token,
            "music_assistant", "play_media",
            full_entity_id,
            {"media_id": stream_url, "media_type": "track", "enqueue": "play"},
        )
    else:
        result = await ha_client.call_service(
            ha_url, ha_token,
            "media_player", "play_media",
            full_entity_id,
            {"media_content_id": stream_url, "media_content_type": "audio/mp4"},
        )

    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Resuming '{title}'",
            service="audiobookshelf",
        )
    return ExecutionResult(status="FAILURE", message=f"Resume failed: {result.get('error')}", service="audiobookshelf")


async def _handle_progress(abs_url: str, abs_key: str, req) -> ExecutionResult:
    progress = await abs_client.get_items_in_progress(abs_url, abs_key)
    if "error" in progress:
        return ExecutionResult(status="FAILURE", message=progress["error"], service="audiobookshelf")

    items = progress.get("libraryItems", [])
    if not items:
        return ExecutionResult(status="SUCCESS", message="No audiobooks currently in progress.", service="audiobookshelf")

    summaries = []
    for i in sorted(items, key=lambda x: x.get("progressLastUpdate", 0), reverse=True)[:10]:
        media = i.get("media", {})
        meta = media.get("metadata", {})
        if not meta.get("title"):
            summaries.append({
                "title": i.get("title", "Unknown"),
                "author": meta.get("authorName", ""),
                "progress": "0%",
                "time": f"{_format_time(media.get('duration', 0))}",
            })
            continue
        duration = media.get("duration", i.get("duration", 0))
        summaries.append({
            "title": meta.get("title", "Unknown"),
            "author": meta.get("authorName", ""),
            "progress": "0%",
            "time": f"{_format_time(duration)}",
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


async def _handle_last_played(abs_url: str, abs_key: str) -> ExecutionResult:
    """Get recently played audiobooks from Audiobookshelf with full details."""
    try:
        items = await abs_client.get_items_in_progress(abs_url, abs_key)
        library_items = items.get("libraryItems", [])
        if not library_items:
            return ExecutionResult(
                status="SUCCESS",
                message="No recently played audiobooks found.",
                service="audiobookshelf",
                detail={"books": []},
            )

        # Sort by most recently played first
        library_items.sort(key=lambda x: x.get("progressLastUpdate", 0), reverse=True)

        books = []
        for item in library_items[:20]:
            media = item.get("media", {})
            meta = media.get("metadata", {})

            # Build complete author string
            author_name = meta.get("authorName", "") or meta.get("author", "")
            authors = meta.get("authors", [])
            if authors and isinstance(authors, list):
                author_name = ", ".join(a if isinstance(a, str) else a.get("name", "") for a in authors)
            if not author_name:
                author_name = "Unknown"

            # Collect narrator info
            narrators = meta.get("narrators", [])
            narrator_str = ""
            if narrators:
                if isinstance(narrators, list):
                    narrator_str = ", ".join(n if isinstance(n, str) else n.get("name", "") for n in narrators)
                else:
                    narrator_str = str(narrators)
            if not narrator_str:
                narrator_str = meta.get("narratorName", "") or meta.get("narrator", "")

            duration = media.get("duration", item.get("duration", 0))
            last_update = item.get("progressLastUpdate", 0)

            # Fetch individual progress for this book to get actual percentage
            try:
                progress_data = await abs_client.get_book_progress(abs_url, abs_key, item.get("id", ""))
                current_time = progress_data.get("currentTime", 0)
                is_complete = progress_data.get("isComplete", False)
                if duration and duration > 0:
                    pct = min(100, int((current_time / duration) * 100))
                else:
                    pct = progress_data.get("progress", 0)
            except Exception:
                current_time = 0
                is_complete = False
                pct = 0

            # Build chapters info
            chapters = media.get("chapters", [])
            chapters_list = []
            if chapters and isinstance(chapters, list):
                chapters_list = [
                    {"title": c.get("title", ""), "startTime": c.get("startTime", 0)}
                    for c in chapters if c
                ]

            # Build full metadata
            books.append({
                "id": item.get("id", ""),
                "title": meta.get("title", item.get("title", "Unknown")),
                "author": author_name or "Unknown",
                "narrator": narrator_str,
                "publisher": meta.get("publisher", ""),
                "series": meta.get("series", ""),
                "publishedDate": meta.get("publishedDate", ""),
                "publishedYear": meta.get("publishedYear", ""),
                "description": meta.get("description", ""),
                "genres": meta.get("genres", []),
                "tags": meta.get("tags", []),
                "language": meta.get("language", ""),
                "duration": duration,
                "duration_formatted": _format_time(duration) if duration else "",
                "progress": pct,
                "progress_current_time": current_time,
                "is_complete": is_complete,
                "last_played": last_update,
                "last_played_formatted": datetime.fromtimestamp(last_update / 1000).strftime("%Y-%m-%d %H:%M") if last_update else "",
                "library_id": item.get("libraryId", ""),
                "has_podcast": meta.get("isPodcast", False),
                "explicit": meta.get("explicit", False),
                "chapters": chapters_list,
                "chapter_count": len(chapters_list),
                "cover_path": media.get("cover", {}).get("path", "") if isinstance(media.get("cover"), dict) else (media.get("cover", "") or ""),
            })

        return ExecutionResult(
            status="SUCCESS",
            message=f"Retrieved {len(books)} recently played audiobook(s).",
            service="audiobookshelf",
            detail={"books": books},
        )
    except Exception as e:
        log.error(f"[abs.last_played] Error: {e}", exc_info=True)
        return ExecutionResult(
            status="FAILURE",
            message=f"Failed to get last played: {e}",
            service="audiobookshelf",
        )


async def _roku_play_audiobook(roku_entity: str, stream_url: str, title: str, ha_url: str, ha_token: str) -> ExecutionResult:
    """Play audiobook on Roku: ECP launch Media Assistant app + delegate audio to MA sibling."""
    import asyncio

    import aiohttp

    from . import roku as roku_handler

    ma_entity = await roku_handler.find_ma_player_sibling(ha_url, ha_token, roku_entity)
    if not ma_entity:
        return ExecutionResult(status="FAILURE", message=f"No Music Assistant player found for {roku_entity}.", service="audiobookshelf")

    roku_ip = await roku_handler.get_roku_ip(ha_url, ha_token, roku_entity)
    if roku_ip:
        params = {"t": "a", "autoplay": "true", "songName": title}
        ecp_url = f"http://{roku_ip}:8060/launch/{roku_handler.MEDIA_ASSISTANT_CHANNEL_ID}"
        try:
            connector = aiohttp.TCPConnector(verify_ssl=False)
            async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=10)) as client:
                async with client.post(ecp_url, params=params) as resp:
                    if resp.status in (200, 204):
                        await asyncio.sleep(3)
        except Exception as e:
            log.warning(f"[abs.roku] ECP launch failed: {e}")

    result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "play_media", ma_entity,
        {"media_id": stream_url, "media_type": "track", "enqueue": "play"},
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Now playing audiobook: {title}", service="audiobookshelf")
    return ExecutionResult(status="FAILURE", message=f"Audiobook playback failed: {result.get('error')}", service="audiobookshelf")


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
