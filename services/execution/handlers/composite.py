# services/execution/handlers/composite.py
"""
Composite workflows that chain multiple handlers together.
These are "macro-actions" that combine existing capabilities.
"""
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    import ha_client
    from schemas import ExecutionResult
    import storage
    from tts import text_to_speech
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult
    from . import storage
    from ..tts import text_to_speech

log = logging.getLogger("execution.composite")


async def handle_document_broadcast(req) -> ExecutionResult:
    """
    Read a document from Nextcloud storage, summarize it via LLM (passed through),
    and broadcast the summary as TTS to a Home Assistant media_player.

    Expected req fields:
        input_path: Nextcloud path to the text file
        entity_id: HA media_player to broadcast to
        summary: Optional pre-written summary (if not provided, uses first 500 chars)
        voice: Optional TTS voice ID
    """
    ctx = req.user_context
    input_path = getattr(req, "input_path", "")
    entity_id = getattr(req, "entity_id", "")
    summary = getattr(req, "summary", None)
    voice = getattr(req, "voice", None)

    if not input_path:
        return ExecutionResult(status="FAILURE", message="input_path is required.", service="composite_broadcast")
    if not entity_id:
        return ExecutionResult(status="FAILURE", message="entity_id is required.", service="composite_broadcast")

    # Step 1: Read the document from storage
    try:
        from schemas import StorageFileReadRequest
        read_req = StorageFileReadRequest(user_context=ctx, path=input_path)
        read_result = await storage.handle_storage_read(read_req)
        if read_result.status != "SUCCESS":
            return ExecutionResult(
                status="FAILURE",
                message=f"Failed to read document: {read_result.message}",
                service="composite_broadcast",
            )
        content = read_result.message
    except Exception as e:
        return ExecutionResult(status="FAILURE", message=f"Storage read error: {e}", service="composite_broadcast")

    # Step 2: Prepare broadcast text
    if summary:
        broadcast_text = summary
    else:
        broadcast_text = content[:500]
        if len(content) > 500:
            broadcast_text += "... (document truncated)"

    # Step 3: Generate Kokoro TTS audio
    try:
        audio_bytes = await text_to_speech(broadcast_text, voice=voice)
    except Exception as e:
        return ExecutionResult(status="FAILURE", message=f"TTS generation failed: {e}", service="composite_broadcast")

    if not audio_bytes:
        return ExecutionResult(status="FAILURE", message="Kokoro TTS returned empty audio.", service="composite_broadcast")

    # Cache audio in memory and serve via local media server (port 8888)
    from uuid import uuid4
    import time
    audio_cache_key = f"tts-{uuid4().hex[:8]}-{int(time.time())}"
    
    from main import TEMP_AUDIO_CACHE, TEMP_AUDIO_DIR
    import os
    TEMP_AUDIO_CACHE[audio_cache_key] = audio_bytes
    
    # Save to disk for media server FileResponse
    wav_path = os.path.join(TEMP_AUDIO_DIR, f"{audio_cache_key}.wav")
    try:
        with open(wav_path, "wb") as f:
            f.write(audio_bytes)
    except Exception as e:
        log.warning(f"Failed to save TTS to disk: {e}")

    # Build local media URL
    try:
        from config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            raise RuntimeError("EXECUTION_EXTERNAL_HOST not configured")
        media_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{audio_cache_key}"
    except Exception as e:
        return ExecutionResult(status="FAILURE", message=f"Cannot build media URL: {e}", service="composite_broadcast")

    full_entity_id = ha_client.sanitize_entity_id("media_player", entity_id)

    # Power on if off
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    if state and state.get("state") == "off":
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", full_entity_id)
        import asyncio
        await asyncio.sleep(2)

    # Play Kokoro audio via local media server URL
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        full_entity_id,
        {"media_content_id": media_url, "media_content_type": "audio/wav"},
    )

    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Broadcasting document summary to {entity_id}.",
            service="composite_broadcast",
            detail={"source_file": input_path, "broadcast_length": len(broadcast_text)},
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"Broadcast failed: {result.get('error')}",
        service="composite_broadcast",
    )


async def handle_night_mode(req) -> ExecutionResult:
    """
    Night mode: turn off lights, set climate to sleep temp, and optionally
    start an audiobook or sleep sounds on a media player.

    Expected req fields:
        lights: List of light entity_ids to turn off (or "all")
        climate_entity: Climate entity to adjust
        sleep_temp: Target temperature for sleep
        media_entity: Optional media_player for sleep sounds
        media_query: Optional search query for sleep sounds/audiobook
    """
    ctx = req.user_context
    lights = getattr(req, "lights", "all")
    climate_entity = getattr(req, "climate_entity", None)
    sleep_temp = getattr(req, "sleep_temp", 68)
    media_entity = getattr(req, "media_entity", None)
    media_query = getattr(req, "media_query", None)

    results = []

    # Step 1: Turn off lights
    if lights:
        try:
            from schemas import LightControlRequest
            if lights == "all":
                all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
                light_entities = [s["entity_id"] for s in all_states if s["entity_id"].startswith("light.") and s.get("state") == "on"]
            else:
                light_entities = lights if isinstance(lights, list) else [lights]

            for light_id in light_entities:
                light_req = LightControlRequest(user_context=ctx, entity_id=light_id, action="turn_off", brightness_pct=0)
                from handlers import light as light_handler
                r = await light_handler.handle_light(light_req)
                results.append(f"light.{light_id}: {r.message}")
        except Exception as e:
            results.append(f"Lights error: {e}")

    # Step 2: Set climate to sleep temp
    if climate_entity:
        try:
            from handlers import climate as climate_handler
            climate_req = climate_handler.ClimateRequest(
                user_context=ctx,
                entity_id=climate_entity,
                temperature=sleep_temp,
            )
            r = await climate_handler.handle_climate(climate_req)
            results.append(f"Climate: {r.message}")
        except Exception as e:
            results.append(f"Climate error: {e}")

    # Step 3: Optional media playback
    if media_entity and media_query:
        try:
            from schemas import MediaPlayRequest
            media_req = MediaPlayRequest(
                user_context=ctx,
                entity_id=media_entity,
                device_name=None,
                query=media_query,
                media_type=None,
                media_content_id=None,
                media_content_type=None,
                enqueue="replace",
                volume=None,
            )
            from handlers import media as media_handler
            r = await media_handler.handle_media_play(media_req)
            results.append(f"Media: {r.message}")
        except Exception as e:
            results.append(f"Media error: {e}")

    return ExecutionResult(
        status="SUCCESS",
        message="Night mode activated.",
        service="composite_night_mode",
        detail={"actions": results},
    )
