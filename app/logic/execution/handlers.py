from app.settings import GlobalResources, log
from .registry import ActionDispatcher

# Local logic imports
# Note: Since we are inside logic.execution, we can import absolute from 'app.logic...'
from app.logic.calendar_ops import (
    tool_calendar_list, tool_calendar_add, tool_calendar_delete, 
    tool_calendar_update, tool_calendar_read
)
from app.logic.timer_ops import (
    tool_timer_add, tool_timer_list, tool_timer_delete, 
    tool_timer_pause, tool_timer_resume, tool_alarm_add
)
from app.logic.note_ops import (
    tool_note_add, tool_note_append, tool_note_read, tool_note_delete
)
from app.logic.web_search import tool_web_search

from app.domains.media import handle_media_command
from app.logic.music_assistant_ops import tool_list_playlists, tool_list_radio, tool_music_search
from app.logic.android_remote_ops import tool_remote_command, tool_launch_app_android
from app.domains.shared import execute_ha_service

# --- CALENDAR TOOLS ---
@ActionDispatcher.register("calendar_add")
async def handle_calendar_add(query: str, user_creds: dict, model: str, **kwargs):
    res = await tool_calendar_add(query, user_creds, model, GlobalResources.redis_client)
    return {
        "status": "SUCCESS" if "Scheduled" in res.get("message", "") else "FAILURE",
        "message": res.get("message", "Calendar operation failed."),
        "service": "calendar_add",
    }

@ActionDispatcher.register("calendar_list")
async def handle_calendar_list(user_creds: dict, **kwargs):
    res = await tool_calendar_list(user_creds, GlobalResources.redis_client)
    return {
        "status": "SUCCESS",
        "message": res.get("message", "Calendar list failed."),
        "service": "calendar_list",
    }

@ActionDispatcher.register("calendar_delete")
async def handle_calendar_delete(query: str, user_creds: dict, model: str, **kwargs):
    return await tool_calendar_delete(query, user_creds, model, GlobalResources.redis_client)

@ActionDispatcher.register("calendar_update")
async def handle_calendar_update(query: str, user_creds: dict, model: str, **kwargs):
    return await tool_calendar_update(query, user_creds, model, GlobalResources.redis_client)

# --- TIMER/ALARM TOOLS ---
@ActionDispatcher.register("timer_add")
async def handle_timer_add(query: str, user_creds: dict, model: str, params: dict = None, **kwargs):
    return await tool_timer_add(
        query, user_creds, model, GlobalResources.redis_client, GlobalResources.ha_collection, params
    )

@ActionDispatcher.register("alarm_add")
async def handle_alarm_add(query: str, user_creds: dict, model: str, **kwargs):
    return await tool_alarm_add(
        query, user_creds, model, GlobalResources.redis_client, GlobalResources.ha_collection
    )

@ActionDispatcher.register("timer_list")
async def handle_timer_list(user_creds: dict, **kwargs):
    return await tool_timer_list(user_creds, GlobalResources.redis_client)

@ActionDispatcher.register("timer_delete")
async def handle_timer_delete(query: str, user_creds: dict, **kwargs):
    return await tool_timer_delete(query, user_creds, GlobalResources.redis_client)

@ActionDispatcher.register("timer_pause")
async def handle_timer_pause(query: str, **kwargs):
    return await tool_timer_pause(query)

@ActionDispatcher.register("timer_resume")
async def handle_timer_resume(query: str, **kwargs):
    return await tool_timer_resume(query)

# --- MEDIA TOOLS ---
@ActionDispatcher.register("media_command")
async def handle_media_tool(query: str, user_creds: dict, params: dict = None, **kwargs):
    intent = params.get("intent", "turn_on") if params else "turn_on"
    entity_id = params.get("entity_id") if params else None
    device_name = params.get("device_name") if params else None
    brightness = params.get("brightness") if params else None
    
    # Pack remaining params as kwargs to pass through
    extra_kwargs = {k: v for k, v in (params or {}).items() if k not in ["intent", "entity_id", "device_name", "brightness"]}
    
    return await handle_media_command(
        intent,
        query,
        entity_id,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
        device_name=device_name,
        brightness=brightness,
        **extra_kwargs
    )

@ActionDispatcher.register("play_media")
async def handle_play_media(query: str, user_creds: dict, params: dict = None, **kwargs):
    # Propagate params if any
    extra_kwargs = params or {}
    return await handle_media_command(
        "play_media",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
        **extra_kwargs
    )

@ActionDispatcher.register("pause_media")
async def handle_pause_media(query: str, user_creds: dict, params: dict = None, **kwargs):
    """Handle pause using remote Play button for Roku (toggle)"""
    # Get last media entity from Redis context
    user = user_creds.get("user", "admin")
    last_entity_key = f"rag:last_media_entity:{user}"
    last_entity = GlobalResources.redis_client.get(last_entity_key)
    
    if last_entity:
        last_entity = last_entity.decode() if isinstance(last_entity, bytes) else last_entity
        log.info(f"[PAUSE_MEDIA] Using last media entity: {last_entity}")
        
        # Check if it's a Roku device (look for roku in entity_id or check metadata)
        if "roku" in last_entity.lower():
            # Find remote entity for this Roku device
            remote_entity = last_entity.replace("media_player.", "remote.")
            remote_entity = remote_entity.replace("_2n", "_")  # Adjust for Roku naming
            if "_2n" in last_entity:
                # For roku_2n0062385487 → remote.28_tcl_roku_tv
                remote_entity = "remote.28_tcl_roku_tv"
            
            log.info(f"[PAUSE_MEDIA] Sending Play button to remote: {remote_entity}")
            result = await execute_ha_service(
                "remote",
                "send_command",
                remote_entity,
                user_creds,
                {"command": "Play"},
                GlobalResources.redis_client
            )
            return [result]
    
    # Fallback to standard handler
    return await handle_media_command(
        "pause_media",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("media_play")
async def handle_media_play(query: str, user_creds: dict, params: dict = None, **kwargs):
    """Handle resume/play using remote Play button for Roku (toggle)"""
    # Get last media entity from Redis context
    user = user_creds.get("user", "admin")
    last_entity_key = f"rag:last_media_entity:{user}"
    last_entity = GlobalResources.redis_client.get(last_entity_key)
    
    if last_entity:
        last_entity = last_entity.decode() if isinstance(last_entity, bytes) else last_entity
        log.info(f"[MEDIA_PLAY] Using last media entity: {last_entity}")
        
        # Check if it's a Roku device
        if "roku" in last_entity.lower():
            # Find remote entity for this Roku device
            remote_entity = last_entity.replace("media_player.", "remote.")
            if "_2n" in last_entity:
                # For roku_2n0062385487 → remote.28_tcl_roku_tv
                remote_entity = "remote.28_tcl_roku_tv"
            
            log.info(f"[MEDIA_PLAY] Sending Play button to remote: {remote_entity}")
            result = await execute_ha_service(
                "remote",
                "send_command",
                remote_entity,
                user_creds,
                {"command": "Play"},
                GlobalResources.redis_client
            )
            return [result]
    
    # Fallback to standard handler
    return await handle_media_command(
        "media_play",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("stop_media")
@ActionDispatcher.register("media_pause")
async def handle_stop_media(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "stop_media",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("media_next")
@ActionDispatcher.register("media_skip")
async def handle_media_next(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "media_next",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("media_previous")
async def handle_media_previous(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "media_previous",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("turn_on")
async def handle_turn_on(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "turn_on",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("turn_off")
async def handle_turn_off(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "turn_off",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("set_color")
async def handle_set_color(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "set_color",
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

# --- MUSIC ASSISTANT TOOLS ---
@ActionDispatcher.register("list_playlists")
async def handle_list_playlists(query: str, user_creds: dict, **kwargs):
    return await tool_list_playlists(query, user_creds)

@ActionDispatcher.register("list_radio")
async def handle_list_radio(query: str, user_creds: dict, **kwargs):
    return await tool_list_radio(query, user_creds)

@ActionDispatcher.register("music_search")
async def handle_music_search(query: str, user_creds: dict, **kwargs):
    return await tool_music_search(query, user_creds)

# --- ANDROID TV TOOLS ---
@ActionDispatcher.register("remote_command")
async def handle_remote_command(query: str, user_creds: dict, params: dict = None, **kwargs):
    cmd = params.get("command", query)
    entity_id = params.get("entity_id") 
    if not entity_id:
        return {"status": "FAILURE", "message": "No entity specified for remote command."}
        
    return await tool_remote_command(cmd, entity_id, user_creds, GlobalResources.redis_client)

# --- NOTE TOOLS ---
@ActionDispatcher.register("note_add")
async def handle_note_add(query: str, params: dict = None, **kwargs):
    p = params or {}
    res = await tool_note_add(p.get("title", "New Note"), p.get("content", query), p.get("category", "General"))
    return {"status": "SUCCESS" if res.get("status") == "success" else "FAILURE", "message": res.get("msg", ""), "service": "note_add"}

@ActionDispatcher.register("note_append")
async def handle_note_append(query: str, params: dict = None, **kwargs):
    p = params or {}
    res = await tool_note_append(p.get("title", "Shopping List"), p.get("content", query))
    return {"status": "SUCCESS" if res.get("status") == "success" else "FAILURE", "message": res.get("msg", ""), "service": "note_append"}

@ActionDispatcher.register("note_read")
async def handle_note_read(params: dict = None, **kwargs):
    p = params or {}
    res = await tool_note_read(p.get("title", ""))
    return {"status": "SUCCESS" if "Note Content" in res else "FAILURE", "message": res, "service": "note_read"}

@ActionDispatcher.register("note_delete")
async def handle_note_delete(params: dict = None, **kwargs):
    p = params or {}
    res = await tool_note_delete(p.get("title", ""))
    return {"status": "SUCCESS" if "deleted" in res.lower() else "FAILURE", "message": res, "service": "note_delete"}

# --- SEARCH & MISC ---
@ActionDispatcher.register("web_search")
async def handle_web_search(query: str, user_creds: dict = None, model: str = None, **kwargs):
    log.info(f"Executing Tool: web_search for query: {query}")

    # Contextualize the web search query using conversation history (like RAG does)
    if user_creds and model:
        from app.logic.pipeline import contextualize_query
        user = user_creds.get("user", "default")
        refined_query, _, _, _ = await contextualize_query(query, user, model)
        log.info(f"Web search contextualized: '{query}' -> '{refined_query}'")
        query = refined_query

    res = await tool_web_search(query)
    return {"status": "SUCCESS", "message": res, "service": "web_search"}

@ActionDispatcher.register("intent_learn")
async def handle_intent_learn(params: dict = None, **kwargs):
    res = f"Cannot learn '{params.get('phrase', '')}' with current prompt context." if params else "Cannot learn phrase."
    return {"status": "FAILURE", "message": res, "service": "intent_learn"}
