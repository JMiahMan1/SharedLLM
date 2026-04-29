import asyncio
import json
import logging
from urllib.parse import urlparse
import aiohttp

from app.settings import HA_URL, GlobalResources, get_user_creds

log = logging.getLogger(__name__)

async def start_ha_websocket_listener():
    """Connects to Home Assistant websocket to cache real-time state changes in Redis."""
    if not HA_URL or not GlobalResources.redis_client:
        log.warning("WebSocket Listener: HA_URL or Redis not configured. Skipping.")
        return

    creds = get_user_creds("default")
    token = creds.get("ha_token")
    if not token:
        log.warning("WebSocket Listener: No HA token found.")
        return

    parsed = urlparse(HA_URL)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{parsed.netloc}/api/websocket"

    retry_delay = 5
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    log.info("WebSocket Listener: Connected to HA.")
                    
                    # 1. Authentication Phase
                    auth_msg = await ws.receive_json()
                    if auth_msg.get("type") == "auth_required":
                        await ws.send_json({"type": "auth", "access_token": token})
                        auth_ok = await ws.receive_json()
                        if auth_ok.get("type") != "auth_ok":
                            log.error("WebSocket Listener: Authentication failed.")
                            return
                            
                    # 2. Subscribe to state changes
                    subscribe_msg = {
                        "id": 1,
                        "type": "subscribe_events",
                        "event_type": "state_changed"
                    }
                    await ws.send_json(subscribe_msg)
                    
                    log.info("WebSocket Listener: Subscribed to state_changed events.")
                    
                    # 3. Listen for events
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("type") == "event":
                                event_data = data.get("event", {}).get("data", {})
                                entity_id = event_data.get("entity_id")
                                new_state = event_data.get("new_state", {})
                                
                                if entity_id and new_state:
                                    state_str = new_state.get("state")
                                    friendly_name = new_state.get("attributes", {}).get("friendly_name", entity_id)
                                    
                                    # Update Redis Cache Instantly (<1ms latency lookup later)
                                    GlobalResources.redis_client.hset(
                                        f"ha:state:{entity_id}",
                                        mapping={
                                            "state": state_str,
                                            "friendly_name": friendly_name
                                        }
                                    )
                                    # Optional: expire after some time just to be safe
                                    GlobalResources.redis_client.expire(f"ha:state:{entity_id}", 86400)
                                    
        except Exception as e:
            log.warning(f"WebSocket Listener Error: {e}. Retrying in {retry_delay}s...")
            
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)
