from typing import Dict, Any, Optional
import logging
import asyncio
from app.domains.media.integrations.base import MediaIntegration, VideoHelperMixin
from app.domains.shared import execute_ha_service

log = logging.getLogger(__name__)

class RokuIntegration(MediaIntegration, VideoHelperMixin):
    """
    Roku TV Integration.
    Supports:
    - Direct URL Playback (Preferred): Downloads video locally via yt-dlp and casts URL to Roku.
    - App Deep Linking (Fallback): Launches YouTube app with video ID.
    """
    
    service_type = "video"
    creates_wrapper = False # Roku doesn't use generic wrappers like MA
    
    @property
    def integration_type(self) -> str:
        return "roku"
    
    async def get_state(self, entity_id: str, user_creds: Dict) -> Optional[Any]:
        """Get current state of the entity"""
        import requests
        from app.settings import HA_URL
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
            if resp.status_code == 200:
                from types import SimpleNamespace
                data = resp.json()
                return SimpleNamespace(state=data.get("state"), attributes=data.get("attributes", {}))
            return None
        except Exception as e:
            log.warning(f"[Roku] Failed to get state: {e}")
            return None

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Roku via Direct Casting (User Requirement).
        """
        log.info(f"[Roku] Playing Media: {query} (Entity: {entity_id}) - CAST MODE")
        
        # [SmartPowerSync] - Ensure TV display is actually ON
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        # Step 1: Check current state
        initial_state = await self.get_state(entity_id, user_creds)
        current_state_value = initial_state.state if initial_state else "unknown"
        
        log.info(f"[Roku] Current state: {current_state_value}")
        
        # Step 2: If OFF, turn on first
        if current_state_value in ["off", "unavailable", "unknown"]:
            log.info(f"[Roku] Device is {current_state_value}, calling turn_on...")
            await self.turn_on(entity_id, user_creds)
            await asyncio.sleep(3)
        
        # Step 3: Send Home button to wake display (even if state is 'idle')
        # Idle just means standby - Home button actually wakes the screen
        log.info(f"[Roku] Sending Home button to wake display...")
        await execute_ha_service("remote", "send_command", remote_entity_id, user_creds, {"command": "Home"}, None)
        await asyncio.sleep(3)
        
        # Step 4: Verify display is showing something
        final_state = await self.get_state(entity_id, user_creds)
        if final_state:
            log.info(f"[Roku] After wake: state={final_state.state}, app={final_state.attributes.get('app_name', 'N/A')}")
        
        if media_type == "video":
            # 1. Resolve Query
            if not query.startswith(("http", "www", "spotify", "app")):
                from app.logic.web_search import tool_web_search
                search_results_text = await tool_web_search(f"{query} youtube")
                # Parse first URL from results
                import re
                urls = re.findall(r'URL: (https?://[^\s]+)', search_results_text)
                if urls:
                    query = urls[0]
                    log.info(f"[Roku] Resolved to {query}")

            # 2. Download & Cast (Direct Stream)
            # This is mandated by user ("HAVE to do the cast feature").
            local_url = await self._download_and_serve_video(query)
            
            if local_url:
                 log.info(f"[Roku] Streaming: {local_url}")
                 
                 # Get Roku IP from Home Assistant device info
                 roku_ip = await self._get_roku_ip(entity_id, user_creds)
                 if not roku_ip:
                     log.error("[Roku] Could not determine Roku IP address")
                     return {"status": "FAILURE", "message": "Roku IP address not found"}
                 
                 # Use Roku ECP API to launch Roku Media Player with video URL
                 # ECP /input endpoint: http://<roku_ip>:8060/input/15985?t=v&u=<url>&videoName=<name>&videoFormat=mp4
                 import requests
                 from urllib.parse import quote
                 
                 ecp_url = f"http://{roku_ip}:8060/input/15985"
                 params = {
                     "t": "v",  # Type: video
                     "u": local_url,  # Video URL
                     "videoName": "SharedLLM Stream",
                     "videoFormat": "mp4"
                 }
                 
                 try:
                     log.info(f"[Roku ECP] Launching video: {ecp_url} with URL: {local_url}")
                     response = requests.post(ecp_url, params=params, timeout=10)
                     if response.status_code == 200:
                         log.info("[Roku ECP] Successfully launched video")
                         return {
                             "status": "SUCCESS",
                             "message": f"Playing video on {entity_id}",
                             "entity_id": entity_id,
                             "service": "roku_ecp_input"
                         }
                     else:
                         log.error(f"[Roku ECP] Failed with status {response.status_code}: {response.text}")
                         return {
                             "status": "FAILURE",
                             "message": f"Roku ECP returned {response.status_code}",
                             "entity_id": entity_id
                         }
                 except Exception as e:
                     log.error(f"[Roku ECP] Exception: {e}")
                     return {
                         "status": "FAILURE",
                         "message": f"Roku ECP error: {str(e)}",
                         "entity_id": entity_id
                     }
            else:
                log.error("[Roku] Failed to generate local stream URL.")

        # Fallback: Generic playback
        return await execute_ha_service(
            "media_player", 
            "play_media", 
            entity_id, 
            user_creds, 
            {
                "media_content_id": query,
                "media_content_type": media_type 
            }, 
            kwargs.get("redis_client")
        )
    
    async def _get_roku_ip(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """Extract Roku IP address from Home Assistant Device Registry"""
        import requests
        from app.settings import HA_URL
        import os
        from urllib.parse import urlparse
        
        # Check for configured Roku IP
        configured_ip = os.getenv("ROKU_IP")
        if configured_ip:
            log.info(f"[Roku] Using configured IP: {configured_ip}")
            return configured_ip
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            
            # Step 1: Get entity to find device_id
            resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
            if resp.status_code != 200:
                log.error(f"[Roku] Could not fetch entity {entity_id}")
                return None
            
            entity_data = resp.json()
            device_id = entity_data.get("attributes", {}).get("device_id")
            
            if not device_id:
                log.warning(f"[Roku] No device_id found for {entity_id}, trying device registry search")
            
            # Step 2: Get device registry
            resp = requests.get(f"{HA_URL}/api/config/device_registry/list", headers=headers, timeout=5)
            if resp.status_code != 200:
                log.error(f"[Roku] Could not fetch device registry")
                return None
            
            devices = resp.json()
            
            # Step 3: Find device by device_id or by searching for Roku in connections/identifiers
            target_device = None
            if device_id:
                target_device = next((d for d in devices if d.get("id") == device_id), None)
            
            # Fallback: search for TCL/Roku manufacturer
            if not target_device:
                for device in devices:
                    manufacturer = device.get("manufacturer", "").lower()
                    model = device.get("model", "").lower()
                    if "roku" in manufacturer or "tcl" in manufacturer or "roku" in model:
                        # Verify this device has our entity
                        identifiers = device.get("identifiers", [])
                        for identifier_set in identifiers:
                            if len(identifier_set) >= 2 and entity_id in str(identifier_set):
                                target_device = device
                                break
                        if target_device:
                            break
            
            if not target_device:
                log.error(f"[Roku] Could not find device in registry for {entity_id}")
                return None
            
            # Step 4: Extract IP from configuration_url
            config_url = target_device.get("configuration_url")
            if config_url:
                parsed = urlparse(config_url)
                ip = parsed.hostname
                if ip:
                    log.info(f"[Roku] Found IP from configuration_url: {ip}")
                    return ip
            
            log.error(f"[Roku] No configuration_url found in device registry, ROKU_IP env var required")
            return None
            
        except Exception as e:
            log.error(f"[Roku] Error getting IP from device registry: {e}")
            return None

