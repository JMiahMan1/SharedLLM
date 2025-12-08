from settings import GlobalResources, log
from .registry import ActionDispatcher

from logic.calendar_ops import (
    tool_calendar_list, tool_calendar_add, tool_calendar_delete, 
    tool_calendar_update, tool_calendar_read
)
from logic.timer_ops import (
    tool_timer_add, tool_timer_list, tool_timer_delete, 
    tool_timer_pause, tool_timer_resume, tool_alarm_add
)
from logic.note_ops import (
    tool_note_add, tool_note_append, tool_note_read, tool_note_delete
)
from logic.web_search import tool_web_search
from logic.web_search import tool_web_search
from logic.media_ops import handle_media_command
from logic.music_assistant_ops import tool_list_playlists, tool_list_radio, tool_music_search
from logic.android_remote_ops import tool_remote_command, tool_launch_app_android

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
    return await handle_media_command(
        intent,
        query,
        None,
        user_creds,
        GlobalResources.ha_collection,
        GlobalResources.redis_client,
    )

@ActionDispatcher.register("play_media")
async def handle_play_media(query: str, user_creds: dict, params: dict = None, **kwargs):
    return await handle_media_command(
        "play_media",
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
async def handle_web_search(query: str, **kwargs):
    log.info(f"Executing Tool: web_search for query: {query}")
    res = await tool_web_search(query)
    return {"status": "SUCCESS", "message": res, "service": "web_search"}

@ActionDispatcher.register("intent_learn")
async def handle_intent_learn(params: dict = None, **kwargs):
    res = f"Cannot learn '{params.get('phrase', '')}' with current prompt context." if params else "Cannot learn phrase."
    return {"status": "FAILURE", "message": res, "service": "intent_learn"}
