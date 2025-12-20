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
        
        For music requests: Delegates to Music Assistant if entity is MA wrapper.
        For video requests: Uses direct casting via Media Assistant app.
        """
        log.info(f"[Roku] Playing Media: {query} (Entity: {entity_id}) - Type: {media_type}")
        
        # [Music Delegation] If this is a music request AND the entity is an MA wrapper,
        # delegate to MusicAssistantIntegration instead of trying to play music directly
        if media_type == "music":
            # Check if this entity is a Music Assistant wrapper
            from app.settings import GlobalResources
            try:
                # [Robust Lookup] Try strict ID first, then fallback to domain-prefixed (ingestion bug workaround)
                search_ids = [entity_id]
                if entity_id.startswith("media_player."):
                    search_ids.append(f"media_player.{entity_id}")
                
                docs = GlobalResources.ha_collection.get(ids=search_ids, include=["metadatas"])
                
                if docs and docs.get("metadatas"):
                    import json
                    attrs_str = docs["metadatas"][0].get("attributes", "{}")
                    attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                    
                    if attrs.get("mass_player_type"):
                        log.info(f"[Roku] Music request on MA wrapper, delegating to MusicAssistantIntegration")
                        from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
                        ma_integration = MusicAssistantIntegration()
                        return await ma_integration.play_media(entity_id, query, media_type, user_creds, **kwargs)
            except Exception as e:
                log.warning(f"[Roku] Failed to check MA wrapper status: {e}, continuing with direct play")
        
        # [Video Playback] - Direct casting for video content
        log.info(f"[Roku] Using direct video playback mode")
        
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
            # 1. Resolve Query using StandardIntegration's search (same as Cast)
            if not query.startswith(("http", "www", "spotify", "app")):
                from app.domains.media.integrations.standard import StandardIntegration
                std_integration = StandardIntegration()
                cleaned_query = std_integration._clean_query(query, media_type, entity_id, kwargs.get("device_name"))
                resolved_url = await std_integration._search_video_url(cleaned_query)
                if resolved_url:
                    query = resolved_url
                else:
                    log.error("[Roku] No valid video URL found in search results")
                    return {"status": "FAILURE", "message": "Could not find a playable video"}


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
                 
                 
                 # Strategy 1: Direct Play via Media Assistant (App ID 782875) - Preferred
                 # This is a custom receiver that supports direct URL playback without DLNA browsing.
                 direct_result = await self._play_media_direct(roku_ip, local_url, "Casted Video", "mp4")
                 if direct_result:
                     return {
                         "status": "SUCCESS",
                         "message": "Launched via Media Assistant (Direct Play)",
                         "entity_id": entity_id,
                         "service": "roku_direct_launch"
                     }
                 
                 log.warning("[Roku] Direct Play failed, falling back to DLNA (Roku Media Player)...")

                 # Strategy 2: Roku Media Player (2213) with Smart Wait
                 # Parameters: contentId, u (duplicate of contentId), mediaType
                 import requests
                 
                 # App ID 2213 = "Roku Media Player"
                 ecp_url = f"http://{roku_ip}:8060/launch/2213"
                 params = {
                     "contentId": local_url,
                     "u": local_url,
                     "mediaType": "movie"
                 }
                 
                 try:
                     log.info(f"[Roku ECP] Using /launch/2213: {ecp_url}")
                     log.info(f"[Roku ECP] Params: {params}")
                     response = requests.post(ecp_url, params=params, timeout=20)
                     if response.status_code == 200:
                         log.info("[Roku ECP] Successfully sent launch command. Executing navigation macro...")
                         
                         # Execute verified macro with Smart Wait:
                         # 1. Wait for Roku to browse our DLNA server (Signal that UI is ready)
                         # 2. Select -> Select -> Play
                         import time
                         
                         log.info("[Roku ECP] Waiting for DLNA Browse signal (Smart Wait)...")
                         start_wait_time = time.time()
                         dlna_ready = False
                         
                         # Smart Wait Loop (up to 45s)
                         for _ in range(22):
                             try:
                                 # Check local DLNA server status
                                 status_resp = requests.get(f"http://127.0.0.1:11435/dlna/status", timeout=2)
                                 if status_resp.status_code == 200:
                                     last_browse = status_resp.json().get("last_browse_timestamp", 0)
                                     # If browse happened AFTER we started waiting (or very recently)
                                     if last_browse > start_wait_time:
                                         log.info(f"[Roku ECP] DLNA Browse detected! (Waited {time.time() - start_wait_time:.1f}s)")
                                         dlna_ready = True
                                         break
                             except Exception as e:
                                 log.warning(f"[Roku ECP] Status check error: {e}")
                             time.sleep(2)
                         
                         if not dlna_ready:
                             log.warning("[Roku ECP] DLNA Browse signal TIMEOUT. Proceeding blindly...")
                         
                         # Buffer for UI rendering after data load
                         log.info("[Roku ECP] Buffer wait (4s) for UI rendering...")
                         time.sleep(4)
                         
                         log.info("[Roku ECP] Sending Select (1/2)...")
                         requests.post(f"http://{roku_ip}:8060/keypress/Select", timeout=20)
                         time.sleep(2)
                         
                         log.info("[Roku ECP] Sending Select (2/2)...")
                         requests.post(f"http://{roku_ip}:8060/keypress/Select", timeout=20)
                         time.sleep(2)
                         
                         log.info("[Roku ECP] Sending Play...")
                         requests.post(f"http://{roku_ip}:8060/keypress/Play", timeout=20)
                         
                         # Verification: Poll for playback state
                         log.info("[Roku ECP] Verifying playback state...")
                         import xml.etree.ElementTree as ET
                         for _ in range(5):
                             try:
                                 q_resp = requests.get(f"http://{roku_ip}:8060/query/media-player", timeout=5)
                                 if q_resp.status_code == 200:
                                     root = ET.fromstring(q_resp.content)
                                     state = root.get("state")
                                     log.info(f"[Roku ECP] Player State: {state}")
                                     if state in ["play", "buffering", "startup"]:
                                         return {
                                             "status": "SUCCESS", 
                                             "message": f"Roku launched and playback verified (State: {state})"
                                         }
                             except Exception as e:
                                 log.warning(f"[Roku ECP] State check failed: {e}")
                             time.sleep(2)

                         log.warning("[Roku ECP] Navigation complete but playback state not confirmed.")
                         return {
                             "status": "SUCCESS",
                             "message": f"Playing video on {entity_id}",
                             "entity_id": entity_id,
                             "service": "roku_ecp_launch"
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
    
    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Stop media playback on Roku by sending Home key.
        This exits the current app and returns to Roku home screen.
        """
        log.info(f"[Roku] Stopping playback on {entity_id}")
        
        # Get remote entity
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        # Send Home key to stop playback
        from app.domains.shared import execute_ha_service
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Home"},
            kwargs.get("redis_client")
        )
        
        if result.get("status") == "SUCCESS":
            return {
                "status": "SUCCESS",
                "message": "Stopped playback and returned to home screen",
                "entity_id": entity_id,
                "service": "remote.send_command"
            }
        
        return result
    

    async def _get_roku_ip(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """Get Roku IP address using SSDP network discovery"""
        import requests
        from app.settings import HA_URL
        from app.utils.network_discovery import discover_roku_ip
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
            if resp.status_code == 200:
                entity_data = resp.json()
                attributes = entity_data.get("attributes", {})
                
                # Attempt SSDP/Scan discovery
                ip = await discover_roku_ip(attributes)
                if ip:
                    log.info(f"[Roku] Discovered IP: {ip}")
                    return ip
                else:
                    log.error(f"[Roku] SSDP discovery found no Roku devices for {entity_id}")
                    return None
        except Exception as e:
            log.error(f"[Roku] Discovery error: {e}")
            return None

            log.error(f"[Roku] Discovery error: {e}")
            return None

    async def _play_media_direct(self, roku_ip: str, video_url: str, title: str, video_format: str) -> bool:
        """
        Attempt to play media directly using 'Media Assistant' (Channel 782875).
        Returns True if launch command was accepted (200 OK), False otherwise.
        """
        import requests
        
        # Channel ID 782875 = Media Assistant (Store Version)
        # Supports: t=v, u=URL, videoName=..., videoFormat=...
        url = f"http://{roku_ip}:8060/launch/782875"
        
        params = {
            "t": "v",
            "u": video_url,
            "videoName": title,
            "videoFormat": video_format
        }
        
        try:
            log.info(f"[Roku Direct] Launching Media Assistant: {url}")
            log.info(f"[Roku Direct] Params: {params}")
            
            resp = requests.post(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                log.info("[Roku Direct] Launch accepted (200 OK).")
                return True
            else:
                log.warning(f"[Roku Direct] Launch failed: {resp.status_code} - {resp.text}")
                return False
                
        except Exception as e:
            log.error(f"[Roku Direct] Exception: {e}")
            return False
