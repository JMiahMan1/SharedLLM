# services/execution/handlers/audiobookshelf.py
"""
Audiobookshelf (ABS) handler — search, play, resume, and track audiobook progress.
Integrates with Home Assistant media_player for playback on any supported device.
"""
import asyncio
import logging
from datetime import datetime

from services.execution import abs_client, ha_client
from services.execution.schemas import AudiobookshelfRequest, ExecutionResult
from services.shared import ma_library
from services.shared.ma_player import is_music_assistant_player

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
        # Prefer a fresh token; the stored API key may be stale/expired. If the
        # login does not come back we still have the stored key, but say so —
        # silently continuing hides a broken login route behind a working cache.
        fresh = await abs_client.abs_login(abs_url, username, password)
        if fresh:
            abs_key = fresh
        else:
            log.warning(
                "[abs] Could not log in to Audiobookshelf as %s; continuing with the stored API key. "
                "If requests start failing with 401, check the ABS username/password.",
                username,
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

        elif action == "play_podcast_episode":
            return await _handle_play_podcast_episode(abs_url, abs_key, req)

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

    libs_res = await abs_client.get_libraries(abs_url, abs_key)
    if "error" in libs_res:
        return ExecutionResult(status="FAILURE", message=libs_res["error"], service="audiobookshelf")

    libraries = [lib for lib in libs_res.get("libraries", []) if lib.get("id")]
    q = req.query or ""
    search_limit = max(req.limit, 25)

    # BUG-14: server-side search per library, run in parallel — library items
    # are never enumerated (was up to 51x500 items downloaded per query).
    responses = await asyncio.gather(
        *(abs_client.search_library_items(abs_url, abs_key, lib["id"], q, limit=search_limit) for lib in libraries),
        return_exceptions=True,
    )

    book_summaries: list[dict] = []
    podcast_summaries: list[dict] = []
    authors: dict[str, dict] = {}
    seen_ids: set[str] = set()
    matched_author_ids: list[str] = []

    def _unwrap(entry) -> dict | None:
        if isinstance(entry, dict) and isinstance(entry.get("libraryItem"), dict):
            return entry["libraryItem"]
        return entry if isinstance(entry, dict) else None

    def _summary(item: dict) -> dict:
        meta = item.get("media", {}).get("metadata", {})
        title = meta.get("title") or ""
        author = meta.get("authorName") or ""
        narrator = meta.get("narratorName") or ""
        cover = item.get("media", {}).get("coverPath", "")
        if not cover and isinstance(item.get("media", {}).get("cover"), dict):
            cover = item["media"]["cover"].get("path", "")
        return {
            "meta": meta,
            "media": item.get("media", {}),
            "id": item.get("id", ""),
            "title": title,
            "author": author,
            "narrator": narrator,
            "cover": cover,
        }

    def _add_book(item: dict) -> None:
        info = _summary(item)
        if not info["id"] or info["id"] in seen_ids:
            return
        seen_ids.add(info["id"])
        meta, media = info["meta"], info["media"]
        book_summaries.append({
            "id": info["id"],
            "title": info["title"],
            "author": info["author"],
            "narrator": info["narrator"],
            "series": meta.get("seriesName", ""),
            "publishedYear": meta.get("publishedYear", ""),
            "genres": meta.get("genres", []),
            "duration": media.get("duration", 0),
            "duration_formatted": _format_time(media.get("duration", 0)) if media.get("duration") else "",
            "cover": info["cover"],
            "type": "book",
            "source": "library",
        })
        if info["author"] and info["author"].lower() not in authors:
            authors[info["author"].lower()] = {"id": info["author"], "name": info["author"], "type": "author", "source": "library"}

    def _add_podcast(item: dict) -> None:
        info = _summary(item)
        if not info["id"] or info["id"] in seen_ids:
            return
        seen_ids.add(info["id"])
        podcast_summaries.append({
            "id": info["id"],
            "title": info["title"],
            "author": info["author"],
            "description": info["meta"].get("description", ""),
            "cover": info["cover"],
            "type": "podcast",
            "source": "library",
        })

    for lib, resp in zip(libraries, responses):
        if isinstance(resp, BaseException):
            log.warning(f"[abs.search] library {lib.get('id')} search failed: {resp}")
            continue
        if not isinstance(resp, dict) or "error" in resp:
            continue
        is_podcast = "podcast" in (lib.get("mediaType") or "").lower()
        if is_podcast:
            entries = list(resp.get("podcast") or []) + list(resp.get("episodes") or [])
            for entry in entries:
                item = _unwrap(entry)
                if item:
                    _add_podcast(item)
            continue
        for entry in resp.get("book") or []:
            item = _unwrap(entry)
            if item:
                _add_book(item)
        # Series matches carry their books with them.
        for series_match in resp.get("series") or []:
            if isinstance(series_match, dict):
                for item in series_match.get("books") or []:
                    if isinstance(item, dict):
                        _add_book(item)
        # Author matches: render chips (Media.tsx re-searches by name) and
        # remember ids so their books can be fetched without a listing call.
        for author in resp.get("authors") or []:
            if not isinstance(author, dict):
                continue
            name = author.get("name") or ""
            author_id = author.get("id") or ""
            if not name:
                continue
            if name.lower() not in authors:
                authors[name.lower()] = {"id": author_id or name, "name": name, "type": "author", "source": "library"}
            if author_id and author_id not in matched_author_ids and len(matched_author_ids) < search_limit:
                matched_author_ids.append(author_id)

    # Author chips re-search by author name; server title search won't match
    # those books, so fetch each matched author's books via
    # GET /api/authors/:id?include=items (parallel, never an /items listing).
    if matched_author_ids:
        author_responses = await asyncio.gather(
            *(abs_client.get_author(abs_url, abs_key, author_id, include="items") for author_id in matched_author_ids),
            return_exceptions=True,
        )
        for author_resp in author_responses:
            if isinstance(author_resp, BaseException) or not isinstance(author_resp, dict) or "error" in author_resp:
                continue
            for item in author_resp.get("libraryItems") or []:
                if isinstance(item, dict):
                    _add_book(item)

    total = len(book_summaries) + len(podcast_summaries) + len(authors)
    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {total} result(s) for '{req.query}'.",
        service="audiobookshelf",
        detail={
            "books": book_summaries[: req.limit],
            "podcasts": podcast_summaries[: req.limit],
            "authors": list(authors.values())[: req.limit],
            "total": total,
        },
    )


async def _handle_play(abs_url: str, abs_key: str, req) -> ExecutionResult:
    if not req.entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required to play.", service="audiobookshelf")

    if req.book_id:
        book_id = req.book_id
    elif req.query:
        search = await abs_client.search_library(abs_url, abs_key, req.query, limit=1)
        if "error" in search or not search.get("results"):
            return ExecutionResult(status="FAILURE", message=f"No audiobook found for '{req.query}'.", service="audiobookshelf")
        book = search["results"][0]
        book_id = book.get("id")
        if not book_id:
            return ExecutionResult(status="FAILURE", message=f"Search result for '{req.query}' has no id.", service="audiobookshelf")
    else:
        return ExecutionResult(status="FAILURE", message="book_id or query is required.", service="audiobookshelf")

    return await _play_book_session(abs_url, abs_key, req, book_id)


async def _play_book_session(abs_url: str, abs_key: str, req, book_id: str) -> ExecutionResult:
    """Start an ABS 2.x playback session for a book and play its first track.

    Live ABS removed /api/items/:id/stream; book audio is now served through a
    session (POST /api/items/{bookId}/play) whose tracks are 1-based, so the
    index comes from the expanded item rather than a guess.

    There are two genuinely different delivery routes, and picking the wrong one
    is why audiobooks appeared to "play" while nothing came out:

    * **Music Assistant players** stream the book themselves, from MA's own
      Audiobookshelf provider. MA is not a URL player — hand it the gateway HLS
      URL and it files the URL as the track *title*, leaving the player idle. It
      needs MA's own ``library://audiobook/<n>`` URI, so this resolves that first.
    * **Everything else** (Roku, Chromecast, DLNA, TVs) can only be handed a URL,
      so it gets the gateway-routed, mt-token-signed HLS URL (§7.4). The ABS API
      key is never exposed to the device.

    The ABS session is started either way, because the non-MA route needs it and
    starting it also makes MA's provider warm up.
    """
    item = await abs_client.get_book(abs_url, abs_key, book_id)
    if "error" in item:
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not load audiobook '{book_id}': {item['error']}",
            service="audiobookshelf",
        )

    media = item.get("media") or {}
    title = (media.get("metadata") or {}).get("title") or book_id
    tracks = media.get("tracks") or []
    track_index = tracks[0].get("index") if tracks else None
    if not isinstance(track_index, int) or track_index < 1:
        return ExecutionResult(
            status="FAILURE",
            message=f"Audiobook '{title}' has no playable audio track.",
            service="audiobookshelf",
        )

    session = await abs_client.start_playback_session(abs_url, abs_key, book_id, None)
    if "error" in session or not session.get("id"):
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not start a playback session for '{title}': {session.get('error', 'unknown')}",
            service="audiobookshelf",
        )

    stream_url = abs_client.get_session_track_url(session["id"], ctx_user(req), track_index=track_index)
    log.info(f"[abs] Book session started: item={book_id} track={track_index} session={session['id']}")
    return await _dispatch_stream(
        stream_url, "application/x-mpegurl", req, title, abs_item_id=book_id
    )


# How long to wait for a player to actually start moving before calling the
# play a failure. HA service calls return as soon as the *call* is accepted, and
# the accepted call is not the same as playback — an MA player handed an
# unresolvable URL accepts the call and then sits idle forever.
PLAYBACK_VERIFY_ATTEMPTS = 6
PLAYBACK_VERIFY_INTERVAL = 2.0
# States in which a player is doing something with the media we handed it.
PLAYING_STATES = {"playing", "buffering"}
# A title that is really our stream URL means the player treated the URL as
# metadata — the exact MA failure this module works around. Used to explain a
# failed verify instead of reporting a bare "did not start".
_URL_TITLE_MARKERS = ("http://", "https://", "?user=", "&mt=", "abs-session")


def _looks_like_stream_url(value: str | None) -> bool:
    if not value:
        return False
    low = value.lower()
    return any(marker in low for marker in _URL_TITLE_MARKERS)


async def _verify_playback(
    ha_url: str, ha_token: str, entity_id: str, attempts: int | None = None
) -> tuple[bool, str]:
    """Poll a player until it is playing something, and describe why not.

    Returns ``(ok, detail)``. ``detail`` is a short operator-facing explanation
    used in the FAILURE message, so a silent non-start is never reported as
    success.
    """
    # Resolved here rather than as a default argument so the constant stays
    # overridable at call time.
    attempts = PLAYBACK_VERIFY_ATTEMPTS if attempts is None else attempts
    last_state = "unknown"
    last_title = ""
    for attempt in range(attempts):
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if state:
            last_state = state.get("state") or "unknown"
            attrs = state.get("attributes") or {}
            last_title = (attrs.get("media_title") or "") if isinstance(attrs, dict) else ""
            if last_state in PLAYING_STATES:
                if _looks_like_stream_url(last_title):
                    # Playing, but of our URL treated as a title: the player has
                    # no media controller for this. Report it rather than pass.
                    return False, (
                        f"player reported playing '{last_title}', which is our stream URL being "
                        "used as the track title — it cannot resolve this media"
                    )
                return True, last_title
        if attempt < attempts - 1:
            await asyncio.sleep(PLAYBACK_VERIFY_INTERVAL)
    if _looks_like_stream_url(last_title):
        return False, (
            f"player never started ({last_state}) and stored our stream URL as its title "
            f"('{last_title}') — it has no media controller for this media"
        )
    return False, f"player never started playing (state stayed '{last_state}')"


async def _dispatch_stream(
    stream_url: str,
    content_type: str,
    req,
    title: str,
    abs_item_id: str | None = None,
) -> ExecutionResult:
    """Send media to a player (power-on, Roku, MA, or direct) and confirm it plays."""
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    ha_url = ctx_ha_url(req)
    ha_token = ctx_ha_token(req)

    state = await ha_client.get_state(ha_url, ha_token, full_entity_id)
    if state and state.get("state") == "off":
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", full_entity_id)
        await asyncio.sleep(2)

    # Detect if this is a Roku device — needs two-step MASS flow
    from . import roku as roku_handler
    is_roku = await roku_handler.is_roku_device(ha_url, ha_token, full_entity_id)

    if is_roku:
        return await _roku_play_audiobook(full_entity_id, stream_url, title, ha_url, ha_token)

    # Detect if this is a Music Assistant player
    is_ma = False
    if state:
        is_ma = is_music_assistant_player(
            state.get("attributes", {}), full_entity_id
        )

    if is_ma:
        media_id, ma_note = await _ma_media_id(req, abs_item_id, title)
        if media_id is None:
            return ExecutionResult(
                status="FAILURE",
                message=f"Could not play '{title}' on Music Assistant: {ma_note}",
                service="audiobookshelf",
            )
        log.info(f"[abs] Playing on MA player '{full_entity_id}' via music_assistant.play_media: {media_id}")
        result = await ha_client.call_service(
            ha_url, ha_token,
            "music_assistant", "play_media",
            full_entity_id,
            {"media_id": media_id, "enqueue": "play"},
        )
    else:
        result = await ha_client.call_service(
            ha_url, ha_token,
            "media_player", "play_media",
            full_entity_id,
            {"media_content_id": stream_url, "media_content_type": content_type},
        )

    if not result.get("ok"):
        return ExecutionResult(
            status="FAILURE", message=f"Playback failed: {result.get('error')}", service="audiobookshelf"
        )

    # A 200 from HA only means the service call was accepted. Verify the player
    # actually engaged, so a no-op is reported as a failure with a real reason.
    ok, detail = await _verify_playback(ha_url, ha_token, full_entity_id)
    if not ok:
        log.warning(f"[abs] Playback did not start on '{full_entity_id}': {detail}")
        return ExecutionResult(
            status="FAILURE",
            message=f"'{title}' was accepted by {full_entity_id} but did not start: {detail}",
            service="audiobookshelf",
        )
    return ExecutionResult(status="SUCCESS", message=f"Now playing: {title}", service="audiobookshelf")


async def _ma_media_id(req, abs_item_id: str | None, title: str) -> tuple[str | None, str]:
    """Get the media_id Music Assistant can actually resolve for this book.

    Returns ``(media_id, note)``; ``media_id`` is None when MA cannot be asked or
    does not know the item, and ``note`` explains why. ``media_id`` may be a
    ``library://audiobook/<n>`` URI when the ABS item id is known, or a bare
    ``library://`` URI for a podcast episode (podcast episodes have no ABS
    library item id to map from).
    """
    mass_url = ctx_mass_url(req)
    mass_token = ctx_mass_token(req)

    if abs_item_id:
        try:
            resolved = await ma_library.resolve_audiobook_uri(
                mass_url, mass_token, abs_item_id, title
            )
        except ma_library.MALibraryLookupError as exc:
            return None, str(exc)
        return resolved.ma_uri, ""

    # Podcast episode: MA streams the podcast from its own provider too, but there
    # is no ABS item id to map from, so MA's own search is the only route.
    if not mass_token:
        return None, (
            "Music Assistant credentials are not configured, so its library cannot be "
            "resolved. Set MA_URL and MA_TOKEN."
        )
    try:
        resolved = await ma_library.resolve_audiobook_uri(
            mass_url, mass_token, "", title, limit=5, allow_idless=True
        )
    except ma_library.MALibraryLookupError as exc:
        return None, str(exc)
    return resolved.ma_uri, ""


def _pick_episode(episodes: list[dict], query: str | None) -> dict:
    """Pick the requested episode (case-insensitive title match) or the latest."""
    eps = [e for e in episodes if isinstance(e, dict) and e.get("id")]
    if not eps:
        return {}
    if query:
        q = query.lower().strip()
        if q:
            for e in eps:
                if q in (e.get("title") or "").lower():
                    return e
    return max(eps, key=lambda e: e.get("publishedAt") or 0)


async def _handle_play_podcast_episode(abs_url: str, abs_key: str, req) -> ExecutionResult:
    """Play a podcast episode through an ABS 2.x playback session.

    Live ABS removed /api/items/:id/stream; audio now comes from a session
    (POST /api/items/:id/play/:episodeId) via /public/session/:sid/track/:i,
    which 302-redirects to an HLS playlist. The device gets a gateway-routed,
    mt-token-signed URL for that playlist (§7.4).
    """
    if not req.book_id:
        return ExecutionResult(status="FAILURE", message="book_id (podcast item ID) is required to play an episode.", service="audiobookshelf")
    if not req.entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required to play.", service="audiobookshelf")

    item = await abs_client.get_book(abs_url, abs_key, req.book_id)
    if "error" in item:
        return ExecutionResult(status="FAILURE", message=f"Could not load podcast: {item['error']}", service="audiobookshelf")
    episodes = item.get("media", {}).get("episodes") or []

    if req.episode_id:
        episode = next((e for e in episodes if e.get("id") == req.episode_id), None)
        if not episode:
            return ExecutionResult(
                status="FAILURE",
                message=f"Episode '{req.episode_id}' not found on podcast '{req.book_id}'.",
                service="audiobookshelf",
            )
    else:
        episode = _pick_episode(episodes, req.query)
        if not episode:
            return ExecutionResult(
                status="FAILURE",
                message=f"No episodes found for podcast '{req.book_id}'.",
                service="audiobookshelf",
            )

    session = await abs_client.start_playback_session(abs_url, abs_key, req.book_id, episode["id"])
    if "error" in session or not session.get("id"):
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not start a playback session for episode '{episode.get('title') or episode['id']}': {session.get('error', 'unknown')}",
            service="audiobookshelf",
        )

    stream_url = abs_client.get_session_track_url(session["id"], ctx_user(req))
    log.info(f"[abs] Podcast episode session started: item={req.book_id} episode={episode['id']} session={session['id']}")
    # A podcast episode id is not an ABS library item id, so `abs_item_id` is
    # deliberately omitted: _ma_media_id then resolves MA's own search result for
    # the episode title instead of trying to map a non-existent item.
    return await _dispatch_stream(stream_url, "application/x-mpegurl", req, episode.get("title") or "Podcast episode")


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
    if not item_id:
        return ExecutionResult(status="FAILURE", message="In-progress item has no id.", service="audiobookshelf")

    result = await _play_book_session(abs_url, abs_key, req, item_id)
    if result.status != "SUCCESS":
        return result
    return ExecutionResult(
        status="SUCCESS",
        message=result.message.replace("Now playing: ", "Resuming ", 1),
        service="audiobookshelf",
    )


def _progress_pct(record: dict) -> int:
    """Percent (0-100) from a /api/me/progress mediaProgress record."""
    if not record:
        return 0
    duration = record.get("duration") or 0
    current = record.get("currentTime") or 0
    if duration and duration > 0:
        return max(0, min(100, int(round(current / duration * 100))))
    progress = record.get("progress")
    if isinstance(progress, (int, float)):
        pct = progress * 100 if 0 < progress <= 1 else progress
        return max(0, min(100, int(round(pct))))
    return 0


async def _all_progress_by_item(abs_url: str, abs_key: str) -> dict:
    """One upstream call: GET /api/me/progress -> {libraryItemId: mediaProgress}.

    Returns {} when ABS reports an error or the call fails so callers degrade
    to 0% instead of failing the listing (BUG-13: was one call per book).
    """
    try:
        data = await abs_client.get_progress(abs_url, abs_key)
    except Exception as e:
        log.warning(f"[abs] media progress fetch failed: {e}")
        return {}
    if not isinstance(data, dict) or "error" in data:
        detail = data.get("error") if isinstance(data, dict) else data
        log.warning(f"[abs] media progress unavailable: {detail}")
        return {}
    records = data.get("mediaProgress") or []
    return {r["libraryItemId"]: r for r in records if isinstance(r, dict) and r.get("libraryItemId")}


async def _handle_progress(abs_url: str, abs_key: str, req) -> ExecutionResult:
    progress = await abs_client.get_items_in_progress(abs_url, abs_key)
    if "error" in progress:
        return ExecutionResult(status="FAILURE", message=progress["error"], service="audiobookshelf")

    items = progress.get("libraryItems", [])
    if not items:
        return ExecutionResult(status="SUCCESS", message="No audiobooks currently in progress.", service="audiobookshelf")

    progress_by_item = await _all_progress_by_item(abs_url, abs_key)
    summaries = []
    for i in sorted(items, key=lambda x: x.get("progressLastUpdate", 0), reverse=True)[:10]:
        media = i.get("media", {})
        meta = media.get("metadata", {})
        pct = _progress_pct(progress_by_item.get(i.get("id", ""), {}))
        if not meta.get("title"):
            summaries.append({
                "title": i.get("title", "Unknown"),
                "author": meta.get("authorName", ""),
                "progress": f"{pct}%",
                "time": f"{_format_time(media.get('duration', 0))}",
            })
            continue
        duration = media.get("duration", i.get("duration", 0))
        summaries.append({
            "title": meta.get("title", "Unknown"),
            "author": meta.get("authorName", ""),
            "progress": f"{pct}%",
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
        progress_by_item = await _all_progress_by_item(abs_url, abs_key)

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

            # Real progress from the single GET /api/me/progress join (BUG-13)
            record = progress_by_item.get(item.get("id", ""), {})
            current_time = record.get("currentTime", 0) or 0
            is_complete = bool(record.get("isFinished", False))
            pct = _progress_pct(record)

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


def ctx_mass_url(req) -> str:
    return getattr(req.user_context, "mass_url", "") or ""


def ctx_mass_token(req) -> str:
    return getattr(req.user_context, "mass_token", "") or ""


def ctx_user(req) -> str:
    return getattr(req.user_context, "user", "") or ""
