# services/execution/handlers/media.py
import asyncio
import logging
import re
from collections.abc import Callable
from typing import cast

from services.common import ma_identity
from services.config import MASS_CONFIG_ENTRY_ID
from services.execution import abs_session_state, activity, ha_client, ha_health, media_vocabulary
from services.execution.media_links import media_file_url
from services.execution.schemas import AudiobookshelfRequest, ExecutionResult, MediaPlayRequest
from services.shared.ma_player import is_music_assistant_player

log = logging.getLogger("execution.media")

# Media type detection patterns
VIDEO_PATTERNS = [r"youtube\.com/watch", r"youtube\.com", r"youtu\.be", r"vimeo\.com", r"rumble\.com", r"dailymotion\.com", r"tiktok\.com", r"twitch\.tv", r"\bvideo\b"]
AUDIOBOOK_PATTERNS = [r"audiobookshelf", r"abs", r"audiobook", r"book\s+"]
PODCAST_PATTERNS = [r"podcast", r"episode", r"show\s+", r"itunes\.apple\.com"]
# Music comes from Music Assistant (URI-based), no URL patterns needed
MUSIC_PATTERNS = []

def detect_media_type(query: str, media_type_hint: str | None = None) -> str:
    """Detect media type from query content and hints."""
    if media_type_hint and media_type_hint in ("music", "video", "podcast", "audiobook", "radio", "url", "announcement"):
        return media_type_hint

    if not query:
        return "music"

    query_lower = query.lower()

    # Check if query is a URL
    if query_lower.startswith(("http://", "https://")):
        for pattern in MUSIC_PATTERNS:
            if re.search(pattern, query_lower):
                return "music"
        for pattern in PODCAST_PATTERNS:
            if re.search(pattern, query_lower):
                return "podcast"
        for pattern in VIDEO_PATTERNS:
            if re.search(pattern, query_lower):
                return "video"
        return "url"

    # Check for media type keywords in query
    for pattern in VIDEO_PATTERNS:
        if re.search(pattern, query_lower):
            return "video"

    # Check for audiobook indicators
    if any(kw in query_lower for kw in ["audiobook", "read by", "narrated by", "chapter"]):
        return "audiobook"

    # Check for podcast indicators
    if any(kw in query_lower for kw in ["podcast", "episode of", "the daily show", "joe rogan"]):
        return "podcast"

    # Default to music
    return "music"

async def _routing_index(ha_url: str, ha_token: str, purpose: str):
    """The hub's capability index (None for a plain HA, logged), when HA is configured and up."""
    if not ha_url or not ha_token or ha_health.is_down(ha_url):
        return None
    from services.execution import capability_index

    return await capability_index.routing_index(ha_url, ha_token, purpose)


async def resolve_room_player(ctx, name, purpose) -> str | None:
    """Resolve an area/device ``name`` to a player entity id, or None.

    Uses the hub's capability index (area registry + per-area player choice).
    Never invents an entity id; returns None when the room or a player cannot
    be resolved.
    """
    if not name:
        return None
    ha_url = getattr(ctx, "ha_url", None)
    ha_token = getattr(ctx, "ha_token", None)
    try:
        idx = await _routing_index(ha_url, ha_token, purpose or "room player")
        states = await ha_client.get_states(ha_url, ha_token) or [] if ha_url else []
        if idx is not None:
            area_id = idx.area_in_text(name)
            if area_id is not None:
                picked = idx.pick_player(area_id, name, purpose, states)
                if picked:
                    return picked
    except Exception as exc:
        log.warning(f"[media/resolve_room_player] could not resolve {name!r}: {exc}")
    return None


async def resolve_entity(req: MediaPlayRequest, ha_url: str, ha_token: str, media_type: str | None = None,
                         may_use: Callable[[str], bool] | None = None) -> str:
    """Resolve entity_id from device_name if needed.

    With the hub's capability index a device name that names a room ("the den",
    "den TV") resolves to the area by its registry name or alias, and the player
    there that can do the job; with no device named, the choice is among the
    players the index says can play media. A plain Home Assistant (no index)
    keeps the old name and domain matching, logged.

    ``may_use``: with no device named, choose only among the players it allows
    (a caller on the hub's own login must not be handed someone else's player).
    """
    allowed = may_use or (lambda _eid: True)
    if req.entity_id:
        return ha_client.sanitize_entity_id("media_player", req.entity_id)

    idx = await _routing_index(ha_url, ha_token, "media player choice")
    states: list[dict] = []
    if idx is not None:
        states = await ha_client.get_states(ha_url, ha_token) or []

    if req.device_name:
        if idx is not None:
            area_id = idx.area_in_text(req.device_name)
            if area_id is not None:
                picked = idx.pick_player(area_id, req.device_name, media_type, states)
                if picked is None:
                    raise ValueError(f"Nothing in {idx.area_name(area_id)} can play media")
                log.info(f"[media/play] '{req.device_name}' -> area {area_id} -> {picked}")
                return picked
        entity_id = await ha_client.resolve_entity_by_name(ha_url, ha_token, req.device_name, "media_player", media_type)
        if entity_id:
            return entity_id
        if not ha_url or ha_health.is_down(ha_url):
            # No HA to look the name up in: use the id HA would give it. The
            # direct Music Assistant route matches players on this same slug.
            return ha_client.sanitize_entity_id("media_player", req.device_name)

    if idx is not None:
        players = [e for e in idx.media_targets() if allowed(e)]
        if not players:
            raise ValueError("No device you may use can play media" if may_use
                             else "The hub has no device that can play media")
        from services.execution.capability_index import state_rank

        by_state = {s.get("entity_id"): s.get("state") for s in states}
        return sorted(players, key=lambda e: (state_rank(by_state.get(e)),
                                              0 if (idx.get(e) or {}).get("available") else 1, e))[0]

    # Fallback to active or available media_player from Home Assistant
    states = await ha_client.get_states(ha_url, ha_token)
    states = [s for s in states or [] if allowed(s.get("entity_id", ""))]
    if states:
        for s in states:
            eid = s.get("entity_id", "")
            if eid.startswith("media_player.") and s.get("state") in ("playing", "paused"):
                return eid
        for s in states:
            eid = s.get("entity_id", "")
            if eid.startswith("media_player.") and s.get("state") in ("idle", "on"):
                return eid
        for s in states:
            eid = s.get("entity_id", "")
            if eid.startswith("media_player."):
                return eid

    raise ValueError("entity_id or device_name is required")

async def resolve_mass_entity(ctx, original_entity: str) -> str:
    """Resolve a media_player entity to its Music Assistant variant.

    Returns the original entity if it's already an MA player with active_queue.
    With the hub's capability index the MA player is the one whose queue drives
    this player, else one on the same device or in the same area; a plain HA
    (no index) matches friendly names. Falls back to the original entity.
    """
    idx = await _routing_index(ctx.ha_url, ctx.ha_token, "Music Assistant player choice")
    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return original_entity

    if idx is not None and original_entity in idx:
        if idx.integration(original_entity) == "music_assistant":
            return original_entity
        queues = {s.get("entity_id"): (s.get("attributes") or {}).get("active_queue") for s in all_states}
        ma_players = [e for e in idx.media_targets() if idx.integration(e) == "music_assistant"]
        driving = [e for e in ma_players if queues.get(e) == original_entity]
        near = idx.nearby(original_entity, any_of=("play_media",), integration="music_assistant")
        same_device = [e for e in near if idx.device_of(e) and idx.device_of(e) == idx.device_of(original_entity)]
        # Another device's Music Assistant player in the same room is that device, not this one: a
        # satellite's own speaker asked for music must not be swapped for the room's TV. A neighbour
        # counts only when it carries this player's name (MA's twin of an HA player has the same name).
        names = {s.get("entity_id"): str((s.get("attributes") or {}).get("friendly_name") or "") for s in all_states}
        own_name = _plain_name(names.get(original_entity, ""))
        same_name = [e for e in near if e not in same_device and own_name
                     and _names_match(own_name, _plain_name(names.get(e, "")))]
        for eid in driving + same_device + same_name:
            log.info(f"[media/play] Resolved MASS entity: {original_entity} -> {eid} (capability index)")
            return eid
        skipped = [e for e in near if e not in same_device]
        if skipped:
            log.info(f"[media/play] {original_entity}: no Music Assistant player of its own; not borrowing "
                     f"{', '.join(skipped)} from the same room")
        return original_entity

    # Check if original entity is already an MA player with active_queue
    for state in all_states:
        if state.get("entity_id") == original_entity:
            attrs = state.get("attributes", {})
            active_queue = attrs.get("active_queue")

            if is_music_assistant_player(
                attrs, original_entity, require_active_queue=True
            ):
                log.info(f"[media/play] Original entity {original_entity} is already an MA player (queue: {active_queue})")
                return original_entity

            original_friendly = attrs.get("friendly_name", "")
            break
    else:
        # Original entity not found in states
        return original_entity

    if not original_friendly:
        return original_entity

    search = original_friendly.lower()

    # Look for MA player with matching friendly name
    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        if eid == original_entity:
            continue

        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        active_queue = attrs.get("active_queue")
        is_ma_player = is_music_assistant_player(attrs, eid)

        # For MA players, allow any state (idle/playing/paused) - active_queue not required
        if is_ma_player and search in friendly:
            log.info(f"[media/play] Resolved MASS entity: {original_entity} -> {eid} (queue={active_queue})")
            return eid

    # No MA variant found, return original (may still work for non-MA players)
    return original_entity

async def _not_on_music_assistant(ctx, entity_id: str) -> ExecutionResult | None:
    """A plain answer when ``entity_id`` has no Music Assistant player, instead of the hub's 400.

    Only with the hub's capability index, which says which integration a player belongs to: a voice
    satellite's own speaker (an ESPHome media_player) plays music only through the Music Assistant
    player on its device, which exists once the satellite has connected to Music Assistant.
    """
    idx = await _routing_index(ctx.ha_url, ctx.ha_token, "Music Assistant player check")
    if idx is None or entity_id not in idx or idx.integration(entity_id) == "music_assistant":
        return None
    states = await ha_client.get_states(ctx.ha_url, ctx.ha_token) or []
    name = next((str((s.get("attributes") or {}).get("friendly_name") or "") for s in states
                 if s.get("entity_id") == entity_id), "") or entity_id
    log.info(f"[media/play] {entity_id} has no Music Assistant player; not asking for music there")
    return ExecutionResult(
        status="FAILURE", service="media_play",
        message=f"{name} is not connected to Music Assistant, so I can't play music on it. "
                "Once it shows up as a player in Music Assistant, ask again, or name another speaker.",
        detail={"route": "none", "reason": "no_music_assistant_player", "entity_id": entity_id},
    )


def _plain_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split())


def _names_match(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or a in b or b in a)


async def _is_ma_entity(ctx, entity_id: str) -> bool:
    """True when `entity_id` is a Music Assistant player (it has an MA queue)."""
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id)
    return bool(state) and is_music_assistant_player(state.get("attributes"), entity_id)


async def play_music_direct(req: MediaPlayRequest, entity_id: str, ctx, why: str) -> ExecutionResult:
    """Play on a speaker straight through Music Assistant, without Home Assistant.

    The HA path (music_assistant.search / play_media) only relays to MA, which
    is its own server; when HA is unreachable MA can still be asked directly.
    The speaker is found among MA's players by the entity's name, and the
    query is searched over MA's WebSocket like the browse widgets do.
    """
    from . import mass_client

    prefix = f"Home Assistant is unreachable ({why})"
    if not (ctx.mass_url and ctx.mass_token):
        return ExecutionResult(
            status="FAILURE",
            message=f"{prefix}, and there is no Music Assistant for direct playback: "
                    f"{ma_identity.unavailable_message(ctx)}",
            service="media_play", detail={"route": "none"},
        )
    query = (req.query or "").strip()
    try:
        player = mass_client.match_player(await mass_client.get_players(ctx.mass_url, ctx.mass_token), entity_id)
        if player is None:
            return ExecutionResult(
                status="FAILURE",
                message=f"{prefix} and Music Assistant has no available player matching {entity_id}.",
                service="media_play", detail={"route": "none"},
            )
        if "://" in query:
            uri, kind = query, "item"
        elif not query:
            # "Play music": a library playlist (the HA route also honours the default-music setting).
            import random

            playlists = [p for p in await mass_client.get_playlists(ctx.mass_url, ctx.mass_token) if p.get("uri")]
            if not playlists:
                return ExecutionResult(status="FAILURE", service="media_play",
                                       message="There's no playlist in the library to play. Ask for an artist "
                                               "or a song.")
            media_vocabulary.remember("playlist", (p.get("name") for p in playlists))
            pick = random.choice(playlists)
            uri, kind, query = pick["uri"], "playlist", pick.get("name") or "a playlist"
        else:
            hit = mass_client.best_uri(
                await mass_client.search(ctx.mass_url, ctx.mass_token, query, limit=10, library_only=False)
            )
            better = None if hit else media_vocabulary.correct(query, "artist", "playlist")
            if better and media_vocabulary.squash(better) != media_vocabulary.squash(query):
                hit = mass_client.best_uri(
                    await mass_client.search(ctx.mass_url, ctx.mass_token, better, limit=10, library_only=False)
                )
                query = better if hit else query
            if not hit:
                return ExecutionResult(status="FAILURE", message=f"No match for '{query}'.", service="media_play")
            uri, kind = hit
        await mass_client.play_media(ctx.mass_url, ctx.mass_token, player["player_id"], uri, req.enqueue or "replace")
    except (mass_client.MACommandError, mass_client.MASearchError) as e:
        return ExecutionResult(
            status="FAILURE", message=f"{prefix} and the direct Music Assistant route failed too: {e}",
            service="media_play", detail={"route": "none"},
        )
    name = player.get("display_name") or player.get("name") or entity_id
    log.info(f"[media/play] '{query}' -> MA player {player['player_id']} directly (HA unreachable: {why})")
    return ExecutionResult(
        status="SUCCESS",
        message=f"Playing '{query}' ({kind}) on {name} directly through Music Assistant (the home hub is down).",
        service="media_play",
        detail={"route": "mass_direct", "player_id": player["player_id"], "uri": uri},
    )


async def play_music(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play music via Music Assistant (through HA, or directly when HA is down)."""
    if not ctx.ha_url or ha_health.is_down(ctx.ha_url):
        return await play_music_direct(req, entity_id, ctx, "down" if ctx.ha_url else "not configured")
    result = await _play_music_via_ha(req, entity_id, ctx)
    # HA can go down between the checks above and the call (the first failures
    # are what mark it down): the HA route then reports "unreachable".
    if result.status != "SUCCESS" and (
        ha_health.is_down(ctx.ha_url) or ha_health.UNREACHABLE.lower() in (result.message or "").lower()
    ):
        return await play_music_direct(req, entity_id, ctx, result.message or "unreachable")
    return result


async def _play_music_via_ha(req: MediaPlayRequest, entity_id: str, ctx, corrected: bool = False) -> ExecutionResult:
    """Play music via Music Assistant through Home Assistant's integration."""
    from . import roku as roku_handler

    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)

    # Samsung Tizen TV: use MASS search for music, then play URL via play_media
    from . import samsung as samsung_handler
    is_samsung = await samsung_handler.is_samsung_tv(ctx.ha_url, ctx.ha_token, entity_id)

    # Resolve MA config entry at runtime if not seeded
    mass_entry = MASS_CONFIG_ENTRY_ID
    if not mass_entry:
        mass_entry = await ha_client.find_mass_config_entry(ctx.ha_url, ctx.ha_token)

    if is_roku:
        log.info(f"[media/play] Detected Roku device, using Roku music handler for query '{req.query}'")
        return await roku_handler.roku_play_music(
            ctx.ha_url, ctx.ha_token, entity_id, req.query or "", mass_entry, enqueue=req.enqueue or "replace",
        )

    mass_entity = await resolve_mass_entity(ctx, entity_id)
    log.info(f"[media/play] play_music configuration: entity_id={entity_id}, mass_entity={mass_entity}, mass_entry={mass_entry}, is_samsung={is_samsung}")
    if mass_entity == entity_id and not is_samsung:
        not_on_ma = await _not_on_music_assistant(ctx, entity_id)
        if not_on_ma:
            return not_on_ma

    if req.query:
        # Check if the query is already a direct Music Assistant URI
        if "://" in req.query:
            log.info(f"[media/play] Query looks like a direct URI: {req.query}. Playing directly.")
            # A Music Assistant item (a playlist, album, track) belongs in the
            # speaker's MA queue whenever the speaker has one; only a plain web
            # address goes to the TV's own player.
            ma_item = not req.query.lower().startswith(("http://", "https://"))
            if is_samsung and not (ma_item and await _is_ma_entity(ctx, mass_entity)):
                log.info("[media/play] Samsung TV detected, playing MASS URL via play_media")
                return await samsung_handler.play_music(
                    ctx.ha_url, ctx.ha_token, entity_id, req.query,
                )

            result = await ha_client.call_service(
                ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                {"media_id": req.query, "enqueue": req.enqueue or "replace", **ma_identity.act_as(ctx)},
            )
            log.info(f"[media/play] play_media service call response for direct URI: {result}")
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Playing media on {entity_id}.", service="media_play")
            else:
                log.error(f"[media/play] play_media direct call failed: {result.get('error')}")
                return ExecutionResult(status="FAILURE", message=f"Failed to play direct URI: {result.get('error')}", service="media_play")

        log.info(f"[media/play] Searching MASS for '{req.query}' on {entity_id}")
        search_result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
            service_data={
                "config_entry_id": mass_entry,
                "name": req.query,
                "media_type": ["track", "artist", "album", "playlist", "radio"],
                "limit": 5,
                **ma_identity.act_as(ctx),
            },
            return_response=True,
        )

        log.info(f"[media/play] MASS search response: {search_result}")
        if ha_health.is_unreachable_result(search_result):
            return ExecutionResult(status="FAILURE", message=str(search_result.get("error")), service="media_play")
        if search_result.get("ok") and search_result.get("service_response"):
            raw = search_result["service_response"]
            resp = raw.get("service_response", raw)
            uri = None
            media_type_label = ""
            for category in ["tracks", "albums", "artists", "playlists", "radio"]:
                items = resp.get(category, [])
                log.info(f"[media/play] Category '{category}' items count: {len(items)}")
                if items:
                    uri = items[0].get("uri")
                    media_type_label = category
                    log.info(f"[media/play] Best match in category '{category}': name='{items[0].get('name')}', uri='{uri}'")
                    break

            if uri:
                log.info(f"[media/play] MASS search resolved URI: {uri} ({media_type_label})")

                # Samsung TV: play the MA URL directly via play_media
                if is_samsung:
                    log.info("[media/play] Samsung TV detected, playing MASS URL via play_media")
                    samsung_res = await samsung_handler.play_music(
                        ctx.ha_url, ctx.ha_token, entity_id, uri,
                    )
                    log.info(f"[media/play] Samsung play result: {samsung_res}")
                    return samsung_res

                log.info(f"[media/play] Calling music_assistant.play_media on '{mass_entity}' with media_id='{uri}'")
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    # As the person: MA then resumes and records the play under their own account.
                    {"media_id": uri, "enqueue": req.enqueue or "replace", **ma_identity.act_as(ctx)},
                )
                log.info(f"[media/play] play_media service call response: {result}")
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' ({media_type_label}) on {entity_id}.", service="media_play")
                else:
                    log.error(f"[media/play] play_media service call failed: {result.get('error')}")
                    return ExecutionResult(status="FAILURE", message=f"Failed to play '{req.query}': {result.get('error')}", service="media_play")

        # A misheard name ("brand in Lake") gets one more search, for the library name it most
        # likely was; never a random track (BUG-25: they asked for something specific).
        if not corrected:
            better = await _corrected_music_query(ctx, req.query, mass_entry)
            if better:
                return await _play_music_via_ha(req.model_copy(update={"query": better}), entity_id, ctx,
                                                 corrected=True)
        log.warning(f"[media/play] MASS search returned no usable match for query='{req.query}' (config_entry={mass_entry}); returning FAILURE")
        return ExecutionResult(status="FAILURE", message=f"No match for '{req.query}'.", service="media_play")

    # "Play music": nothing named, so something sensible from the library.
    return await _play_something(req, entity_id, mass_entity, mass_entry, ctx)


#: Identity setting: what "play music" plays when nothing is named (a Music Assistant URI, or a
#: playlist or artist name to search). Empty shuffles a library playlist, else library tracks.
DEFAULT_MUSIC_SETTING = "voice_default_music"
#: How many library tracks a shuffle of the whole library queues.
SHUFFLE_TRACKS = 25


async def _global_setting(key: str) -> str:
    """One Identity setting's value ("" when unset or Identity cannot be asked, logged)."""
    import aiohttp

    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client, client.get(
            f"{IDENTITY_SVC_URL}/api/settings", headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            if resp.status != 200:
                log.warning(f"[media/play] Identity answered {resp.status} reading {key}")
                return ""
            entries = await resp.json()
    except Exception as e:
        log.warning(f"[media/play] could not read {key} from Identity: {e}")
        return ""
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("key") == key:
            return str(entry.get("value") or "").strip()
    return ""


async def _library_names(ctx, mass_entry: str, kind: str, limit: int) -> list[dict]:
    from . import mass_ha_client

    items = await mass_ha_client.get_library(ctx.ha_url, ctx.ha_token, kind, limit=limit, mass_entry_id=mass_entry)
    vocab_kind = {"artist": "artist", "playlist": "playlist"}.get(kind)
    if vocab_kind and items:
        media_vocabulary.remember(vocab_kind, (i.get("name") for i in items))
    return items


async def _corrected_music_query(ctx, query: str | None, mass_entry: str) -> str | None:
    """The library artist or playlist a misheard ``query`` most likely named, or None."""
    if not query or "://" in query:
        return None
    try:
        if media_vocabulary.stale("artist"):
            await _library_names(ctx, mass_entry, "artist", 500)
        if media_vocabulary.stale("playlist"):
            await _library_names(ctx, mass_entry, "playlist", 200)
    except Exception as e:  # a correction is a second chance, never a new failure
        log.warning(f"[media/play] could not list the library to check a misheard name: {e}")
    better = media_vocabulary.correct(query, "artist", "playlist")
    if better and media_vocabulary.squash(better) != media_vocabulary.squash(query):
        log.info(f"[media/play] no match for '{query}'; trying the library name '{better}'")
        return better
    return None


async def _play_something(req: MediaPlayRequest, entity_id: str, mass_entity: str, mass_entry: str,
                          ctx) -> ExecutionResult:
    """Music when none was named: the household's default, else a shuffled library playlist, else library tracks."""
    import random

    default = await _global_setting(DEFAULT_MUSIC_SETTING)
    if default:
        log.info(f"[media/play] nothing named; playing the default music setting '{default}'")
        return await _play_music_via_ha(req.model_copy(update={"query": default}), entity_id, ctx, corrected=True)

    async def play(media_id, label: str) -> ExecutionResult:
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
            {"media_id": media_id, "enqueue": req.enqueue or "replace", **ma_identity.act_as(ctx)},
        )
        if not result.get("ok"):
            log.error(f"[media/play] playing {label} failed: {result.get('error')}")
            return ExecutionResult(status="FAILURE", message=f"Failed to play {label}: {result.get('error')}",
                                   service="media_play")
        shuffle = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "shuffle_set",
                                               mass_entity, {"shuffle": True})
        if not shuffle.get("ok"):
            log.info(f"[media/play] {mass_entity} did not take shuffle: {shuffle.get('error')}")
        return ExecutionResult(status="SUCCESS", message=f"Playing {label} on shuffle.", service="media_play",
                               detail={"route": "library_shuffle", "media_id": media_id})

    try:
        playlists = [p for p in await _library_names(ctx, mass_entry, "playlist", 200) if p.get("uri")]
        if playlists:
            pick = random.choice(playlists)
            log.info(f"[media/play] nothing named; shuffling library playlist '{pick.get('name')}'")
            return await play(pick["uri"], f"your playlist {pick.get('name') or 'from the library'}")
        tracks = [t["uri"] for t in await _library_names(ctx, mass_entry, "track", 200) if t.get("uri")]
        if tracks:
            log.info(f"[media/play] nothing named and no playlists; shuffling {min(len(tracks), SHUFFLE_TRACKS)} "
                     "library tracks")
            return await play(random.sample(tracks, min(len(tracks), SHUFFLE_TRACKS)), "music from your library")
    except Exception as e:
        log.error(f"[media/play] could not read the music library: {e}")
        return ExecutionResult(status="FAILURE", message=f"I couldn't read the music library: {e}",
                               service="media_play")
    return ExecutionResult(
        status="FAILURE",
        message="There's no music in the library to play yet. Ask for an artist, a song or a playlist, "
                f"or set a default ({DEFAULT_MUSIC_SETTING}) in Admin settings.",
        service="media_play",
    )


async def play_video(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play video via yt-dlp stream. Routes through Roku ECP for Roku devices."""
    from . import roku as roku_handler
    from . import video as video_handler

    query = req.query or req.media_content_id or ""

    # Check if already a URL
    video_url = video_handler.extract_video_url(query)
    if not video_url:
        video_url = await video_handler.search_youtube(query)
        if not video_url:
            return ExecutionResult(status="FAILURE", message=f"Could not find video for '{query}'.", service="media_play")

    # Roku: download optimized format with progressive playback, then launch via ECP
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
    if is_roku:
        log.info("[media.video] Detected Roku device, using Roku ECP handler with progressive download")
        # Wake device in parallel with download
        wake_task = asyncio.create_task(roku_handler.roku_wake_device(ctx.ha_url, ctx.ha_token, entity_id))
        media_id, title = await video_handler.download_video_progressive(video_url)
        if not media_id:
            return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")
        await wake_task
        from services.config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
        stream_url = media_file_url(media_id, ctx.user, EXECUTION_EXTERNAL_HOST)
        return await roku_handler.roku_play_video(
            ctx.ha_url, ctx.ha_token, entity_id, stream_url, title or query,
        )

    # Android TV: delegate video to Cast sibling (non-MA) for reliable playback
    from . import android_tv as android_tv_handler
    is_android_tv = await android_tv_handler.is_android_tv(ctx.ha_url, ctx.ha_token, entity_id)
    if is_android_tv:
        log.info(f"[media.video] Android TV detected ({entity_id}), delegating to android_tv handler")
        return await android_tv_handler.play_video(ctx.ha_url, ctx.ha_token, entity_id, video_url, query, ctx.user)

    # Samsung Tizen TV: use dedicated handler with WOL wake and play_media
    from . import samsung as samsung_handler
    is_samsung = await samsung_handler.is_samsung_tv(ctx.ha_url, ctx.ha_token, entity_id)
    if is_samsung:
        log.info(f"[media.video] Samsung Tizen TV detected ({entity_id}), using samsung handler")
        download_task = asyncio.create_task(video_handler.download_video_progressive(video_url))
        wake_task = asyncio.create_task(samsung_handler.wake_device(ctx.ha_url, ctx.ha_token, entity_id))

        media_id, title = await download_task
        if not media_id:
            return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")
        await wake_task

        from services.config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
        stream_url = media_file_url(media_id, ctx.user, EXECUTION_EXTERNAL_HOST)
        return await samsung_handler.play_video(ctx.ha_url, ctx.ha_token, entity_id, stream_url, title or query)

    # Cast/WebOS: stop active session, ensure TV is on, then play
    # Note: handle_media_play already handles power-on, so we skip redundant turn_on here
    # Start download in parallel with HA setup to save time
    download_task = asyncio.create_task(video_handler.download_video_progressive(video_url))

    # Stop any active session first (e.g., Music Assistant) to prevent conflicts
    log.info(f"[media.video] Stopping active session on {entity_id} before video playback")
    try:
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "media_stop", entity_id)
    except Exception as e:
        log.warning(f"[media.video] media_stop failed (device may not be playing): {e}")
    await asyncio.sleep(1)

    # Volume safeguard: unmute and set to safe level
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id)
    if state:
        attrs = state.get("attributes", {})
        if attrs.get("is_volume_muted"):
            log.info("[media.video] Device is muted, unmuting")
            await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_mute", entity_id, {"is_volume_muted": False})
            await asyncio.sleep(1)
        vol = attrs.get("volume_level")
        if vol is not None and vol < 0.2:
            log.info(f"[media.video] Volume too low ({vol}), boosting to 20%")
            await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", entity_id, {"volume_level": 0.2})
            await asyncio.sleep(1)

    # Wait for download to complete (may already be done if HA setup took longer)
    media_id, title = await download_task
    if not media_id:
        return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")

    from services.config import EXECUTION_EXTERNAL_HOST
    if not EXECUTION_EXTERNAL_HOST:
        return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
    stream_url = media_file_url(media_id, ctx.user, EXECUTION_EXTERNAL_HOST)
    log.info(f"[media.video] Casting video to {entity_id}: {stream_url}")

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": stream_url, "media_content_type": "video/mp4"},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing video '{title or query}' on {entity_id}.", service="media_play")

    return ExecutionResult(status="FAILURE", message=f"Failed to play video on {entity_id}.", service="media_play")

async def play_podcast(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play podcast via Music Assistant or URL."""
    from . import roku as roku_handler

    if req.query and req.query.startswith(("http://", "https://")):
        # Direct podcast URL
        is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
        if is_roku:
            ma_entity = await roku_handler.find_ma_player_sibling(ctx.ha_url, ctx.ha_token, entity_id)
            if ma_entity:
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", ma_entity,
                    {"media_id": req.query, "media_type": "track", "enqueue": "play"},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing podcast on {entity_id}.", service="media_play")
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
            {"media_content_id": req.query, "media_content_type": "audio"},
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=f"Playing podcast on {entity_id}.", service="media_play")

    # Generate search query variations (e.g. "Saint" vs "St.", stripping "podcast")
    q_clean = (req.query or "").strip()
    search_queries = [q_clean]
    q_alt = re.sub(r'\bSaint\b', 'St.', q_clean, flags=re.IGNORECASE)
    q_alt = re.sub(r'\bpodcast\b', '', q_alt, flags=re.IGNORECASE).strip()
    if q_alt and q_alt not in search_queries:
        search_queries.append(q_alt)
    q_no_pod = re.sub(r'\bpodcast\b', '', q_clean, flags=re.IGNORECASE).strip()
    if q_no_pod and q_no_pod not in search_queries:
        search_queries.append(q_no_pod)

    # 1. Search Audiobookshelf for podcasts
    try:
        from services.execution.schemas import AudiobookshelfRequest

        from . import audiobookshelf as abs_handler
        abs_uctx = ctx.user_context if hasattr(ctx, "user_context") else ctx
        podcasts = []
        for sq in search_queries:
            abs_res = await abs_handler.handle_audiobookshelf(
                AudiobookshelfRequest(
                    action="search",
                    query=sq,
                    entity_id=entity_id,
                    user_context=abs_uctx,
                )
            )
            if abs_res.status == "SUCCESS" and abs_res.detail:
                podcasts = abs_res.detail.get("podcasts", [])
                if podcasts:
                    break
        if podcasts:
            pod = podcasts[0]
            pod_id = pod.get("id")
            pod_title = pod.get("title", req.query)
            log.info(f"[media/podcast] Found podcast in Audiobookshelf: '{pod_title}' (id={pod_id})")
            # BUG-26: play a real episode (latest, or one matching the query),
            # not the podcast item itself; the legacy item stream route is
            # gone on live ABS 2.x.
            play_res = await abs_handler.handle_audiobookshelf(
                AudiobookshelfRequest(
                    action="play_podcast_episode",
                    book_id=pod_id,
                    query=req.query,
                    entity_id=entity_id,
                    user_context=abs_uctx,
                )
            )
            if play_res.status == "SUCCESS":
                return ExecutionResult(status="SUCCESS", message=f"Playing podcast '{pod_title}' from Audiobookshelf on {entity_id}.", service="media_play")
    except Exception as e:
        log.debug(f"[media/podcast] Audiobookshelf podcast check error: {e}")

    # 2. Search Music Assistant for podcast
    mass_entry = MASS_CONFIG_ENTRY_ID
    if not mass_entry:
        mass_entry = await ha_client.find_mass_config_entry(ctx.ha_url, ctx.ha_token)

    # Try MASS search for podcast
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
    if is_roku:
        return await roku_handler.roku_play_music(
            ctx.ha_url, ctx.ha_token, entity_id, req.query or "", mass_entry, enqueue=req.enqueue or "replace",
        )

    mass_entity = await resolve_mass_entity(ctx, entity_id)
    search_result = {}
    for sq in search_queries:
        search_result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
            service_data={
                "config_entry_id": mass_entry,
                "name": sq,
                "media_type": ["podcast", "episode"],
                "limit": 5,
            },
            return_response=True,
        )
        if search_result.get("ok") and search_result.get("service_response"):
            raw = search_result["service_response"]
            resp = raw.get("service_response", raw)
            if any(resp.get(cat) for cat in ["podcasts", "episodes"]):
                break

    if search_result.get("ok") and search_result.get("service_response"):
        raw = search_result["service_response"]
        resp = raw.get("service_response", raw)
        for category in ["podcasts", "episodes"]:
            items = resp.get(category, [])
            if items:
                uri = items[0].get("uri")
                if uri:
                    result = await ha_client.call_service(
                        ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                        {"media_id": uri, "enqueue": "play"},
                    )
                    if result.get("ok"):
                        return ExecutionResult(status="SUCCESS", message=f"Playing podcast '{req.query}' on {entity_id}.", service="media_play")

    # Fallback: try searching as track if no podcast/episode results
    log.info(f"[media/podcast] No podcast/episode results found, trying track search for '{req.query}'")
    track_search = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
        service_data={
            "config_entry_id": mass_entry,
            "name": req.query,
            "media_type": ["track"],
            "limit": 5,
        },
        return_response=True,
    )
    if track_search.get("ok") and track_search.get("service_response"):
        raw = track_search["service_response"]
        resp = raw.get("service_response", raw)
        items = resp.get("tracks", [])
        if items:
            uri = items[0].get("uri")
            if uri:
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    {"media_id": uri, "enqueue": "play"},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' as track on {entity_id}.", service="media_play")

    # Fallback 2: Check Nextcloud storage for audio/podcast files
    try:
        from ..http_client import request as http_request
        from ..nextcloud_client import resolve_credentials, webdav_url
        # BUG-26: ctx IS the UserContext; the old `.user_context` read raised
        # AttributeError (swallowed below), silently killing this fallback.
        nc_url, nc_user, nc_pass = resolve_credentials(ctx)
        if nc_url and nc_user and nc_pass:
            podcasts_url = webdav_url(nc_url, nc_user, "Podcasts")
            resp = await http_request("PROPFIND", podcasts_url, auth=(nc_user, nc_pass), headers={"Depth": "1"}, timeout=10, verify=False)
            if resp.get("status_code") in (200, 207):
                xml_text = resp.get("text", "")
                query_slug = re.sub(r'[^a-zA-Z0-9]+', '', (req.query or "").lower())
                import xml.etree.ElementTree as ET
                root = ET.fromstring(xml_text)
                for elem in root.iter():
                    if elem.tag.endswith("href") and elem.text:
                        href = elem.text
                        clean_href = re.sub(r'[^a-zA-Z0-9]+', '', href.lower())
                        if query_slug in clean_href and any(href.lower().endswith(ext) for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav")):
                            full_file_url = f"{nc_url.rstrip('/')}{href}"
                            log.info(f"[media/podcast] Found podcast in Nextcloud: {full_file_url}")
                            res = await ha_client.call_service(
                                ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
                                {"media_content_id": full_file_url, "media_content_type": "music"}
                            )
                            if res.get("ok"):
                                return ExecutionResult(status="SUCCESS", message=f"Playing podcast '{req.query}' from Nextcloud on {entity_id}.", service="media_play")
    except Exception as e:
        log.debug(f"[media/podcast] Nextcloud podcast search error: {e}")

    return ExecutionResult(status="FAILURE", message=f"Could not play podcast '{req.query}' on {entity_id}. Try providing a direct URL.", service="media_play")

async def play_audiobook(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play audiobook via Audiobookshelf."""
    from . import audiobookshelf as abs_handler

    if not req.query:
        return ExecutionResult(status="FAILURE", message="Audiobook title or query is required.", service="media_play")

    # Search ABS
    class _ABSRequest:
        def __init__(self, action, query=None, book_id=None, entity_id=None, limit=10):
            self.user_context = ctx
            self.action = action
            self.query = query
            self.book_id = book_id
            self.entity_id = entity_id
            self.limit = limit

    search_result = await abs_handler.handle_audiobookshelf(
        cast(AudiobookshelfRequest, _ABSRequest("search", query=req.query, limit=5, entity_id=entity_id))
    )

    if search_result.status == "SUCCESS" and search_result.detail:
        books = search_result.detail.get("books", [])
        if books:
            book = books[0]
            book_id = book.get("id")
            # Play the book
            play_result = await abs_handler.handle_audiobookshelf(
                cast(AudiobookshelfRequest, _ABSRequest("play", book_id=book_id, entity_id=entity_id))
            )
            return play_result

    return ExecutionResult(status="FAILURE", message=f"Could not find audiobook '{req.query}'.", service="media_play")

async def play_url(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play a direct URL on the device."""
    url = req.media_content_id or req.query
    content_type = req.media_content_type or "url"

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": url, "media_content_type": content_type},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing URL on {entity_id}.", service="media_play")

    return ExecutionResult(status="FAILURE", message=f"Failed to play URL on {entity_id}.", service="media_play")

async def handle_media_play(req: MediaPlayRequest) -> ExecutionResult:
    """Unified media play handler supporting all content types and devices."""
    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    assert ha_url is not None
    assert ha_token is not None

    log.info(f"[media/play] Incoming request: user={ctx.user} query='{req.query}' entity_id='{req.entity_id}' device_name='{req.device_name}' media_type='{req.media_type}' volume={req.volume} enqueue='{req.enqueue}'")

    # Detect media type BEFORE resolving entity (needed for context-aware entity resolution)
    media_type = detect_media_type(req.query or req.media_content_id or "", req.media_type)
    log.info(f"[media/play] Detected media type: {media_type}")

    # Resolve entity with media context
    try:
        entity_id = await resolve_entity(req, ha_url, ha_token, media_type)
        log.info(f"[media/play] Resolved playback entity: '{entity_id}'")
    except ValueError as e:
        log.error(f"[media/play] Failed to resolve entity: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="media_play")

    try:
        # Set volume if requested
        if req.volume is not None:
            log.info(f"[media/play] Setting volume to {req.volume} on {entity_id}")
            vol_res = await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": req.volume})
            log.info(f"[media/play] Volume set result: {vol_res}")

        # Power on if needed
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if state:
            log.info(f"[media/play] Current state of {entity_id}: state='{state.get('state')}'")
        else:
            log.warning(f"[media/play] Could not retrieve state for {entity_id}")

        if state and state.get("state") in ("off", "standby"):
            log.info(f"[media/play] Device {entity_id} is {state.get('state')}, turning on...")
            on_res = await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id)
            log.info(f"[media/play] Turn on result: {on_res}")
            await asyncio.sleep(2)

        # Route to appropriate handler
        log.info(f"[media/play] Routing playback to handler for media_type='{media_type}'")
        if media_type == "video":
            result = await play_video(req, entity_id, ctx)
        elif media_type == "podcast":
            result = await play_podcast(req, entity_id, ctx)
        elif media_type == "audiobook":
            result = await play_audiobook(req, entity_id, ctx)
        elif media_type == "url":
            result = await play_url(req, entity_id, ctx)
        else:
            result = await play_music(req, entity_id, ctx)

        # Log the play on the person's activity feed (music, video, podcast,
        # audiobook). Best effort: recording is never allowed to change the
        # playback result and never raises.
        if result.status == "SUCCESS":
            feature = {
                "video": "video",
                "podcast": "podcast",
                "audiobook": "audiobook",
            }.get(media_type, "music")
            await activity.record(
                ctx.user,
                feature,
                item=req.query or req.media_content_id or "",
                action="play",
                detail={"entity_id": entity_id},
            )
        return result
    except Exception as e:
        log.error(f"[media/play] Error playing media for {ctx.user} on {entity_id}: {e}", exc_info=True)
        return ExecutionResult(status="FAILURE", message=f"Media play failed: {e!s}", service="media_play")

# Transport commands that act on what is queued rather than on the device.
_QUEUE_COMMANDS = frozenset({
    "play", "resume", "pause", "stop", "next", "previous", "seek", "shuffle_set", "repeat_set",
})


_TRANSPORT_DONE = {
    "next": "Skipped to the next track", "previous": "Went back a track", "pause": "Paused", "stop": "Stopped",
    "play": "Playing", "play_pause": "Toggled play and pause", "seek": "Moved the playback position",
}


def transport_said(command: str, attrs: dict | None, entity_id: str) -> str:
    """What a transport command did, in words a voice can say (never the entity id)."""
    name = (attrs or {}).get("friendly_name") or entity_id.split(".", 1)[-1].replace("_", " ")
    done = _TRANSPORT_DONE.get((command or "").lower())
    return f"{done} on {name}." if done else f"Sent {command} to {name}."


# Audiobook controls that need the ABS session (position, chapters) rather than
# a plain media_player button. Handled before brand fallbacks so a TV entity
# cannot swallow them.
_ABS_ENHANCED_COMMANDS = frozenset({
    "seek_relative", "chapter_next", "chapter_previous", "chapter_goto", "speed", "sleep",
})


def _player_position(attrs: dict | None) -> float:
    """The player's position inside the current track, in seconds."""
    try:
        return max(0.0, float((attrs or {}).get("media_position") or 0))
    except (TypeError, ValueError):
        return 0.0


def _chapter_index(chapters: list[dict], absolute_time: float) -> int:
    for i, chapter in enumerate(chapters):
        start = float(chapter.get("start") or 0)
        end = chapter.get("end")
        end = float(end) if end is not None else None
        if absolute_time >= start and (end is None or absolute_time < end):
            return i
    return -1


async def _abs_on_pause_or_stop(entity_id: str, attrs: dict | None, *, stop: bool) -> None:
    """Write ABS progress back when the person pauses or stops a player we started a book on."""
    position = _player_position(attrs)
    if stop:
        abs_session_state.cancel_sleep(entity_id)
        await abs_session_state.close(entity_id, position)
    else:
        await abs_session_state.sync_position(entity_id, position)


async def _audiobook_transport(
    req, command: str, entity_id: str, attrs: dict | None, state: dict | None
) -> ExecutionResult:
    """Handle seek_relative / chapter_* / speed / sleep for a player."""
    ctx = req.user_context
    info = abs_session_state.active(entity_id)
    chapters = (info or {}).get("chapters") or []
    track_start = float((info or {}).get("track_start") or 0)

    if command == "seek_relative":
        seconds = getattr(req, "seconds", None)
        delta = 30.0 if seconds is None else float(seconds)
        target = max(0.0, _player_position(attrs) + delta)
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "media_seek", entity_id,
            {"seek_position": target},
        )
        if result.get("ok"):
            return ExecutionResult(
                status="SUCCESS",
                message=f"Moved {'forward' if delta >= 0 else 'back'} {abs(delta):.0f} seconds.",
                service="media_transport",
            )
        return ExecutionResult(status="FAILURE", message=f"Seek failed: {result.get('error')}", service="media_transport")

    if command in ("chapter_next", "chapter_previous", "chapter_goto"):
        if not chapters:
            return ExecutionResult(
                status="FAILURE",
                message="Chapter information is not available for this playback.",
                service="media_transport",
            )
        index = _chapter_index(chapters, track_start + _player_position(attrs))
        if command == "chapter_goto":
            wanted = getattr(req, "chapter", None)
            if not wanted or not 1 <= wanted <= len(chapters):
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Chapter {wanted} is out of range (1 to {len(chapters)}).",
                    service="media_transport",
                )
            index = wanted - 1
        elif command == "chapter_next":
            index = min(len(chapters) - 1, max(0, index + 1))
        else:
            index = max(0, index - 1)
        chapter = chapters[index]
        target = max(0.0, float(chapter.get("start") or 0) - track_start)
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "media_seek", entity_id,
            {"seek_position": target},
        )
        if result.get("ok"):
            label = chapter.get("title") or f"chapter {index + 1}"
            return ExecutionResult(status="SUCCESS", message=f"Went to {label}.", service="media_transport")
        return ExecutionResult(status="FAILURE", message=f"Chapter change failed: {result.get('error')}", service="media_transport")

    if command == "speed":
        rate = getattr(req, "rate", None)
        if rate is None:
            return ExecutionResult(
                status="FAILURE", message="Speed requires a rate between 0.5 and 3.0.", service="media_transport"
            )
        rate = float(rate)
        if is_music_assistant_player(attrs or {}, entity_id) and ctx.mass_url and ctx.mass_token:
            from . import mass_client

            queue_id = (
                (attrs or {}).get("active_queue")
                or (attrs or {}).get("queue_id")
                or entity_id.split(".", 1)[-1]
            )
            try:
                await mass_client.ma_command(
                    ctx.mass_url, ctx.mass_token, "player_queues/set_speed",
                    {"queue_id": queue_id, "speed": rate},
                )
                return ExecutionResult(
                    status="SUCCESS", message=f"Playback speed set to {rate:g}x.", service="media_transport"
                )
            except Exception as e:
                log.warning(f"[media/transport] Could not set MA queue speed: {e}")
        return ExecutionResult(
            status="FAILURE",
            message="Speed is not supported on this speaker.",
            service="media_transport",
        )

    # command == "sleep"
    end_of_chapter = bool(getattr(req, "end_of_chapter", False))
    minutes = getattr(req, "minutes", None)
    if end_of_chapter:
        absolute = track_start + _player_position(attrs)
        index = _chapter_index(chapters, absolute)
        end = chapters[index].get("end") if 0 <= index < len(chapters) else None
        delay = max(1.0, float(end) - absolute) if end is not None else 30 * 60
        when = "at the end of the chapter"
    elif minutes:
        delay = max(0.0, float(minutes) * 60)
        when = f"in {float(minutes):g} minutes"
    else:
        return ExecutionResult(
            status="FAILURE",
            message="Sleep requires minutes or end_of_chapter.",
            service="media_transport",
        )

    async def _pause_player() -> None:
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "media_pause", entity_id)

    abs_session_state.schedule_sleep(entity_id, delay, _pause_player)
    return ExecutionResult(
        status="SUCCESS",
        message=f"Sleep timer set: playback will pause {when}.",
        service="media_transport",
    )


async def handle_media_transport(req) -> ExecutionResult:
    """Transport/remote commands: through HA, or straight to the device when HA is down."""
    ctx = req.user_context
    # Resolve an area/device name to a player when the caller did not pin one
    # (e.g. a voice satellite speaking for its room). Never invent an entity id.
    if not req.entity_id and getattr(req, "device_name", None):
        try:
            req.entity_id = await resolve_room_player(ctx, req.device_name, None)
        except Exception as exc:
            log.warning(f"[media/transport] could not resolve device_name {req.device_name!r}: {exc}")
            req.entity_id = None
    if not req.entity_id and getattr(req, "device_name", None):
        return ExecutionResult(
            status="FAILURE",
            message=f"I couldn't find a speaker in {req.device_name}.",
            service="media_transport",
        )
    if not req.entity_id:
        req.entity_id = ""
    if not ctx.ha_url or ha_health.is_down(ctx.ha_url):
        return await _transport_direct(req, "down" if ctx.ha_url else "not configured")
    result = await _transport_via_ha(req)
    if result.status != "SUCCESS" and (
        ha_health.is_down(ctx.ha_url) or ha_health.UNREACHABLE.lower() in (result.message or "").lower()
    ):
        return await _transport_direct(req, "unreachable")
    return result


async def _transport_direct(req, why: str) -> ExecutionResult:
    from .. import tv_direct

    entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    result = await tv_direct.transport(
        entity_id, req.command, req.user_context,
        volume_level=getattr(req, "volume_level", None), muted=getattr(req, "muted", None),
    )
    message = result["message"]
    if not result["ok"]:
        message = f"Home Assistant is unreachable ({why}) and the direct route failed: {message}"
    return ExecutionResult(
        status="SUCCESS" if result["ok"] else "FAILURE", message=message, service="media_transport",
        detail={"route": result["route"]},
    )


#: Transport command -> the capability-index ability that means the entity serves it itself.
_COMMAND_ABILITY = {
    "pause": "pause", "seek": "seek", "volume_set": "volume", "volume_mute": "mute", "mute": "mute", "volume_unmute": "mute",
    "previous": "next_track", "next": "next_track", "stop": "stop", "volume_up": "volume_step",
    "volume_down": "volume_step", "play": "play", "resume": "play", "power_on": "power", "power_off": "power",
}
_FEATURE_BITS = {
    "pause": 1, "seek": 2, "volume_set": 4, "volume_mute": 8, "mute": 8, "volume_unmute": 8,
    "previous": 16, "next": 32, "stop": 4096, "volume_up": 1024,
    "volume_down": 1024, "play": 16384, "resume": 16384,
    "power_on": 128, "power_off": 256,
}


def _standard_supported(idx, entity_id: str, command: str, supported_features: int) -> bool:
    """Whether the entity serves ``command`` itself: from the index, else HA's feature bits (a plain HA)."""
    if idx is not None and entity_id in idx:
        ability = _COMMAND_ABILITY.get(command)
        return ability is not None and idx.has(entity_id, ability)
    bit = _FEATURE_BITS.get(command)
    return bit is not None and bool(supported_features & bit)


async def _transport_via_ha(req) -> ExecutionResult:
    """Handle media transport commands with TV-specific handling for Android TV, WebOS, Samsung, and Roku."""
    from ..announce_handlers import detect_tv_type
    from . import android_tv, roku, samsung, webos

    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    command = req.command.lower()
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)

    # Detect TV platform using centralized detection (the hub's index names the owner when it has one)
    idx = await _routing_index(ha_url, ha_token, "media transport")
    state = await ha_client.get_state(ha_url, ha_token, full_entity_id)
    attrs = state.get("attributes", {}) if state else {}
    tv_type = detect_tv_type(full_entity_id, state.get("state", "unknown") if state else "unknown", attrs,
                             platform=idx.tv_platform(full_entity_id) if idx is not None else None)

    log.info(f"[media/transport] Platform: {tv_type} for {full_entity_id}")

    # Prefer HA's media_player.* services whenever the entity can serve the
    # command itself (the index's abilities; a plain HA's supported_features
    # bits); brand handlers are the fallback for devices whose media_player
    # entity cannot serve the command (and for remote-only actions like home/back/power).
    supported_features = int(attrs.get("supported_features", 0) or 0)
    standard_supported = _standard_supported(idx, full_entity_id, command, supported_features)

    # Pause / stop close the loop on an ABS session this handler started: write
    # the player's current position back to Audiobookshelf (and close it on
    # stop). Enhanced audiobook controls need the session's chapters, so handle
    # them here, before the TV brand fallbacks can swallow them.
    if command in ("pause", "stop"):
        await _abs_on_pause_or_stop(full_entity_id, attrs, stop=(command == "stop"))
    if command in _ABS_ENHANCED_COMMANDS:
        return await _audiobook_transport(req, command, full_entity_id, attrs, state)

    # A TV or cast speaker playing Music Assistant's stream has its queue on
    # MA's own entity for the same device ("Den TV": the Cast entity
    # cannot skip a track; its MA twin can). Queue commands go there while MA
    # is driving the device, or whenever the device itself cannot do them.
    if command in _QUEUE_COMMANDS and state and not is_music_assistant_player(attrs, full_entity_id):
        sibling = await resolve_mass_entity(ctx, full_entity_id)
        if sibling != full_entity_id:
            sibling_state = await ha_client.get_state(ha_url, ha_token, sibling) or {}
            if sibling_state.get("state") in ("playing", "paused", "buffering") or not standard_supported:
                log.info(f"[media/transport] {command} on {full_entity_id} goes to its Music Assistant player {sibling}")
                full_entity_id = sibling
                attrs = sibling_state.get("attributes", {})
                tv_type = "music_assistant"
                supported_features = int(attrs.get("supported_features", 0) or 0)
                standard_supported = _standard_supported(idx, full_entity_id, command, supported_features)

    # Shuffle/repeat/grouping are ordinary media_player.* services, not
    # feature-gated and never served by a brand remote fallback.
    if command in ("shuffle_set", "repeat_set", "join", "unjoin"):
        if command == "shuffle_set":
            if req.shuffle is None:
                return ExecutionResult(
                    status="FAILURE",
                    message="shuffle_set requires shuffle (true/false).",
                    service="media_transport",
                )
            service_cmd, data = "shuffle_set", {"shuffle": bool(req.shuffle)}
        elif command == "repeat_set":
            if req.repeat is None:
                return ExecutionResult(
                    status="FAILURE",
                    message="repeat_set requires repeat (off/one/all).",
                    service="media_transport",
                )
            service_cmd, data = "repeat_set", {"repeat": req.repeat}
        elif command == "join":
            if not req.group_members:
                return ExecutionResult(
                    status="FAILURE",
                    message="join requires group_members (player entity ids).",
                    service="media_transport",
                )
            service_cmd, data = "join", {"group_members": req.group_members}
        else:
            service_cmd, data = "unjoin", None
        result = await ha_client.call_service(
            ha_url, ha_token, "media_player", service_cmd, full_entity_id, data,
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=transport_said(command, attrs, full_entity_id), service="media_transport")
        return ExecutionResult(status="FAILURE", message=f"Media command failed: {result.get('error')}", service="media_transport", detail=result)

    if not standard_supported:
        if tv_type == "android_tv":
            return await android_tv.send_command(ha_url, ha_token, full_entity_id, command)
        elif tv_type == "webos":
            return await webos.send_command(ha_url, ha_token, full_entity_id, command)
        elif tv_type == "samsung":
            return await samsung.send_key(ha_url, ha_token, full_entity_id, command)
        elif tv_type == "roku":
            return await roku.roku_press(ha_url, ha_token, full_entity_id, command)

    # Standard media transport commands (all devices)
    button_map = {
        "pause": "media_pause", "resume": "media_play", "play": "media_play", "stop": "media_stop",
        "next": "media_next_track", "previous": "media_previous_track",
        "volume_up": "volume_up", "volume_down": "volume_down", "volume_set": "volume_set", "mute": "volume_mute",
        "seek": "media_seek",
        # HA has no "power_off" service; turn_on/turn_off are the real ones.
        "power_on": "turn_on", "power_off": "turn_off",
    }

    service = button_map.get(command, command)
    domain = full_entity_id.split(".")[0]
    target_entity = full_entity_id

    # Remote button commands (uppercase = remote.send_command)
    remote_buttons = {"home", "back", "up", "down", "left", "right", "select", "enter", "ok", "info", "replay", "menu"}
    if command in remote_buttons:
        service_cmd = "send_command"
        data = {"command": command.upper()}
        domain = "remote"
        target_entity = await ha_client.find_companion_remote(ha_url, ha_token, full_entity_id)
    else:
        service_cmd = service
        assert service_cmd is not None
        data = {}

    if command in ("volume_up", "volume_down", "volume_set") and req.volume_level is not None:
        service_cmd = "volume_set"
        data = {"volume_level": req.volume_level}

    if command in ("volume_mute", "mute", "volume_unmute"):
        # HA's volume_mute service requires an explicit target state. Fall back
        # to "mute" when the caller did not say, which matches the old button
        # behaviour of muting on press.
        muted = getattr(req, "muted", None)
        if muted is None:
            muted = False if command == "volume_unmute" else req.volume_level in (None, 0)
        service_cmd = "volume_mute"
        data = {"is_volume_muted": bool(muted)}

    if command == "seek":
        position = getattr(req, "position", None)
        if position is None:
            return ExecutionResult(
                status="FAILURE",
                message="Seek requires a position (seconds).",
                service="media_transport",
            )
        service_cmd = "media_seek"
        data = {"seek_position": float(position)}

    result = await ha_client.call_service(
        ha_url, ha_token, domain, service_cmd, target_entity, data or None,
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=transport_said(command, attrs, full_entity_id), service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Media command failed: {result.get('error')}", service="media_transport", detail=result)

async def handle_tv_cast(req) -> ExecutionResult:
    """Handle TV cast requests (legacy compatibility)."""
    from ..schemas import MediaPlayRequest
    play_req = MediaPlayRequest(
        user_context=req.user_context,
        entity_id=req.media_player_entity_id,
        device_name=None,
        query=None,
        media_type=None,
        media_content_id=req.media_content_id,
        media_content_type=req.media_content_type,
        enqueue="replace",
        volume=None,
    )
    return await handle_media_play(play_req)
