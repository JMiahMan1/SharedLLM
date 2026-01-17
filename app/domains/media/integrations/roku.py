from typing import Dict, Any, Optional
import logging
import asyncio
import aiohttp
import time
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
        from app.settings import HA_URL
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        from types import SimpleNamespace
                        data = await resp.json()
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
        
        if media_type == "music":
            # [Media Assistant Delegation]
            # User Requirement: "play intent is ONLY Music Assistant, by way of Media Assistant on the Roku"
            # Delegate directly to the RokuMediaAssistantIntegration which handles the specific Roku channel (782875)
            # and arguments (t=a) for the rich Music UI.
            
            log.info(f"[Roku] Delegating Music request to RokuMediaAssistantIntegration for Rich UI")
            from app.domains.media.integrations.media_assistant_roku import RokuMediaAssistantIntegration
            ma_roku = RokuMediaAssistantIntegration()
            return await ma_roku.play_media(entity_id, query, media_type, user_creds, **kwargs)

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
                
                # Extract friendly_name from metadata for query cleaning
                device_name = None
                if kwargs.get("metadata"):
                    device_name = kwargs["metadata"].get("friendly_name")
                elif kwargs.get("friendly_name"):
                    device_name = kwargs.get("friendly_name")
                
                log.info(f"[Roku] Extracted device_name: {device_name}")
                
                cleaned_query = std_integration._clean_query(query, media_type, entity_id, device_name)
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
                 
                 if roku_ip:
                     # Strategy 1: Direct Play via Media Assistant (App ID 782875) - Preferred
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
                     
                     # App ID 2213 = "Roku Media Player"
                     ecp_url = f"http://{roku_ip}:8060/launch/2213"
                     params = {
                         "contentId": local_url,
                         "u": local_url,
                         "mediaType": "movie"
                     }
                     
                     try:
                         log.info(f"[Roku ECP] Using /launch/2213: {ecp_url}")
                         async with aiohttp.ClientSession() as session:
                             async with session.post(ecp_url, params=params, timeout=20) as response:
                                 if response.status == 200:
                                     log.info("[Roku ECP] Successfully sent launch command. Executing navigation macro...")
                                     
                                     # Execute verified macro with Smart Wait:
                                     log.info("[Roku ECP] Waiting for DLNA Browse signal (Smart Wait)...")
                                     start_wait_time = time.time()
                                     dlna_ready = False
                                     
                                     # Smart Wait Loop (up to 45s)
                                     for _ in range(22):
                                         try:
                                             # Check local DLNA server status
                                             # Note: keeping this checked via aiohttp too
                                             async with session.get(f"http://127.0.0.1:11435/dlna/status", timeout=2) as status_resp:
                                                 if status_resp.status == 200:
                                                     status_data = await status_resp.json()
                                                     last_browse = status_data.get("last_browse_timestamp", 0)
                                                     if last_browse > start_wait_time:
                                                         log.info(f"[Roku ECP] DLNA Browse detected! (Waited {time.time() - start_wait_time:.1f}s)")
                                                         dlna_ready = True
                                                         break
                                         except Exception as e:
                                             log.warning(f"[Roku ECP] Status check error: {e}")
                                         await asyncio.sleep(2)
                                     
                                     if not dlna_ready:
                                         log.warning("[Roku ECP] DLNA Browse signal TIMEOUT. Proceeding blindly...")
                                     
                                     # Buffer for UI rendering after data load
                                     log.info("[Roku ECP] Buffer wait (4s) for UI rendering...")
                                     await asyncio.sleep(4)
                                     
                                     log.info("[Roku ECP] Sending Select (1/2)...")
                                     async with session.post(f"http://{roku_ip}:8060/keypress/Select", timeout=20): pass
                                     await asyncio.sleep(2)
                                     
                                     log.info("[Roku ECP] Sending Select (2/2)...")
                                     async with session.post(f"http://{roku_ip}:8060/keypress/Select", timeout=20): pass
                                     await asyncio.sleep(2)
                                     
                                     log.info("[Roku ECP] Sending Play...")
                                     async with session.post(f"http://{roku_ip}:8060/keypress/Play", timeout=20): pass
                                     
                                     # Verification: Poll for playback state
                                     log.info("[Roku ECP] Verifying playback state...")
                                     import xml.etree.ElementTree as ET
                                     for _ in range(5):
                                         try:
                                             async with session.get(f"http://{roku_ip}:8060/query/media-player", timeout=5) as q_resp:
                                                 if q_resp.status == 200:
                                                     content = await q_resp.read()
                                                     root = ET.fromstring(content)
                                                     state = root.get("state")
                                                     log.info(f"[Roku ECP] Player State: {state}")
                                                     if state in ["play", "buffering", "startup"]:
                                                         return {
                                                             "status": "SUCCESS", 
                                                             "message": f"Roku launched and playback verified (State: {state})"
                                                         }
                                         except Exception as e:
                                             log.warning(f"[Roku ECP] State check failed: {e}")
                                         await asyncio.sleep(2)
            
                                     log.warning("[Roku ECP] Navigation complete but playback state not confirmed.")
                                     return {
                                         "status": "SUCCESS",
                                         "message": f"Playing video on {entity_id}",
                                         "entity_id": entity_id,
                                         "service": "roku_ecp_launch"
                                     }
                                 else:
                                     log.warning(f"[Roku ECP] Failed with status {response.status}: {await response.text()}")
                     except Exception as e:
                         log.warning(f"[Roku ECP] Exception: {e}")
                 else:
                     log.warning("[Roku] Roku IP address not found. Skipping custom playback strategies.")
                     
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
    

    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn on Roku device using valid wake sequence.
        Standard Roku integration often needs explicit PowerOn to wake panel.
        """
        log.info(f"[Roku] Turning on {entity_id}")
        
        # 1. Standard Turn On
        await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))
        
        # 2. Get Remote Sibling
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # 3. Explicit 'PowerOn' (Critical for wakeup reliability)
        if remote_entity_id:
            log.info(f"[Roku] Sending explicit 'PowerOn' to {remote_entity_id}")
            await execute_ha_service(
                "remote", "send_command", remote_entity_id, user_creds, 
                {"command": "PowerOn"}, kwargs.get("redis_client")
            )
        
        # 4. Follow up with Home (Wake UI)
        await asyncio.sleep(1)
        if remote_entity_id:
             return await execute_ha_service(
                "remote", "send_command", remote_entity_id, user_creds, 
                {"command": "Home"}, kwargs.get("redis_client")
             )
        else:
             # Fallback if no remote
             return await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))

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

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn off Roku device. Note: Roku devices report 'idle' when off/on home screen.
        """
        from app.domains.media.devices import get_entity_state
        
        log.info(f"[Roku] Turning off {entity_id}")
        
        # Check current state - Roku uses 'idle' for off/home screen
        state = await get_entity_state(entity_id, user_creds)
        if state in ["idle", "off", "standby"]:
            log.info(f"[Roku] {entity_id} is already off (state: {state})")
            return {
                "status": "SUCCESS",
                "message": f"Roku is already off.",
                "entity_id": entity_id,
                "service": "turn_off"
            }
        
        # Send Home button to exit app (goes to idle/home = off)
        return await self.stop_media(entity_id, user_creds, **kwargs)
    
    async def _get_roku_remote(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """
        Find the remote entity for this Roku device by checking the same group.
        Similar to Cast's _get_tv_sibling logic.
        """
        from app.settings import GlobalResources
        
        remote_entity = None
        
        # Strategy 1: ChromaDB group lookup
        try:
            if GlobalResources.ha_collection:
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    
                    if current_group_id and current_group_id != "unknown":
                        # Find all devices in same group
                        group_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": current_group_id},
                            include=["metadatas"]
                        )
                        
                        if group_docs and group_docs.get("metadatas"):
                            for metadata in group_docs["metadatas"]:
                                candidate_id = metadata.get("entity_id")
                                candidate_domain = candidate_id.split('.')[0] if candidate_id else None
                                
                                # Find remote entity in the same group
                                if (candidate_domain == "remote" and candidate_id != entity_id):
                                    remote_entity = candidate_id
                                    log.info(f"[Roku] Found remote entity via group: {remote_entity}")
                                    return remote_entity
        except Exception as e:
            log.warning(f"[Roku] ChromaDB lookup failed: {e}")
        
        # Strategy 2: Fallback to simple replacement
        if not remote_entity:
            remote_entity = entity_id.replace("media_player.", "remote.")
            log.info(f"[Roku] Using fallback remote entity: {remote_entity}")
        
        return remote_entity
    
    async def play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Resume/play media on Roku. For Music Assistant, Pause button acts as play/pause toggle.
        """
        log.info(f"[Roku] Resuming playback on {entity_id}")
        
        # Get remote entity from same group
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play key (toggles play/pause for Music Assistant)
        from app.domains.shared import execute_ha_service
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},  # Play button for resume
            kwargs.get("redis_client")
        )
        
        if result.get("status") == "SUCCESS":
            return {
                "status": "SUCCESS",
                "message": "Resumed playback",
                "entity_id": entity_id,
                "service": "remote.send_command"
            }
        
        return result
    
    async def pause_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Pause media on Roku using Pause button.
        """
        log.info(f"[Roku] Pausing playback on {entity_id}")
        
        # Get remote entity from same group
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play key (toggle)
        from app.domains.shared import execute_ha_service
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},  # Play button also pauses (toggle)
            kwargs.get("redis_client")
        )
        
        if result.get("status") == "SUCCESS":
            return {
                "status": "SUCCESS",
                "message": "Paused playback",
                "entity_id": entity_id,
                "service": "remote.send_command"
            }
        
        return result
    
    async def _get_roku_ip(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """Get Roku IP address using SSDP network discovery with robust retries"""
        from app.settings import HA_URL
        from app.utils.network_discovery import discover_roku_ip
        
        # Retry parameters
        max_retries = 6 
        delay = 5
        
        for attempt in range(max_retries):
            try:
                headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5) as resp:
                        if resp.status == 200:
                            entity_data = await resp.json()
                            attributes = entity_data.get("attributes", {})
                            
                            # Attempt SSDP/Scan discovery (ensure this is async-compatible if possible, 
                            # but usually discover_roku_ip is awaited)
                            ip = await discover_roku_ip(attributes)
                            if ip:
                                log.info(f"[Roku] Discovered IP: {ip}")
                                return ip
                            
                            if attempt < max_retries - 1:
                                log.info(f"[Roku] IP not found (Attempt {attempt+1}/{max_retries}). Device might be booting. Waiting {delay}s...")
                                await asyncio.sleep(delay)
                            else:
                                log.error(f"[Roku] SSDP discovery found no Roku devices for {entity_id} after {max_retries} attempts.")
                                return None
            except Exception as e:
                log.error(f"[Roku] Discovery error: {e}")
                if attempt < max_retries - 1:
                     await asyncio.sleep(delay)
                else:
                    return None
        return None



    async def _play_media_direct(self, roku_ip: str, video_url: str, title: str, video_format: str) -> bool:
        """
        Attempt to play media directly using 'Media Assistant' (Channel 782875).
        Returns True if launch command was accepted (200 OK), False otherwise.
        """
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
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        log.info("[Roku Direct] Launch accepted (200 OK).")
                        return True
                    else:
                        log.warning(f"[Roku Direct] Launch failed: {resp.status} - {await resp.text()}")
                        return False
                
        except Exception as e:
            log.error(f"[Roku Direct] Exception: {e}")
            return False

    async def next_track(self, entity_id: str, user_creds: Dict) -> Dict[str, Any]:
        """Skip to next track."""
        domain = entity_id.split(".")[0]
        result = await execute_ha_service(domain, "media_next_track", entity_id, user_creds)

        if result.get("status") == "FAILURE":
            # Attempt fallback to Music Assistant wrapper
            return await self._try_ma_fallback(entity_id, "media_next_track", user_creds) or result
            
        return result

    async def previous_track(self, entity_id: str, user_creds: Dict) -> Dict[str, Any]:
        """Skip to previous track."""
        domain = entity_id.split(".")[0]
        result = await execute_ha_service(domain, "media_previous_track", entity_id, user_creds)

        if result.get("status") == "FAILURE":
            # Attempt fallback to Music Assistant wrapper
            return await self._try_ma_fallback(entity_id, "media_previous_track", user_creds) or result
            
        return result

    async def _find_related_ma_entity(self, original_entity_id: str) -> Optional[str]:
        """Find the corresponding Music Assistant entity for a native device."""
        try:
             # 1. Try common MASS naming pattern: media_player.mass_[name]
             # e.g. media_player.roku_123 -> media_player.mass_roku_123
             name_part = original_entity_id.split(".")[-1]
             # Handle cases where name might be 'living_room_tv' -> 'mass_living_room_tv'
             candidate = f"media_player.mass_{name_part}"
             
             # Verify it exists in our DB/Collection
             from app.settings import GlobalResources
             if GlobalResources.ha_collection:
                 # Check if this candidate exists as an entity
                 docs = GlobalResources.ha_collection.get(ids=[candidate])
                 if docs and docs.get("ids"):
                     return candidate
                     
             # 2. Similarity Search for integration=music_assistant near this name?
             # Might be risky/slow. For now, rely on pattern match availability.
             
        except Exception as e:
            log.warning(f"[Roku] MA lookup error: {e}")
        return None

    async def _try_ma_fallback(self, original_entity_id: str, service: str, user_creds: Dict) -> Any:
        try:
             # Try common MASS naming pattern: media_player.mass_[name]
             name_part = original_entity_id.split(".")[-1]
             candidates = [f"media_player.mass_{name_part}"]
             
             # Also try searching DB for integration=music_assistant
             from app.settings import GlobalResources
             if GlobalResources.ha_collection:
                 docs = GlobalResources.ha_collection.similarity_search(original_entity_id, k=5)
                 for d in docs:
                     if d.metadata.get("integration") == "music_assistant":
                         candidates.append(d.metadata.get("entity_id"))
             
             log.info(f"[Roku] Transport failed on {original_entity_id}. Trying MA candidates: {candidates}")
             
             for cand in candidates:
                 res = await execute_ha_service("media_player", service, cand, user_creds)
                 if res.get("status") == "SUCCESS":
                     log.info(f"[Roku] MA Fallback SUCCESS on {cand}. Updating context.")
                     return res
                     
        except Exception as e:
            log.warning(f"[Roku] MA Fallback error: {e}")
            
        return None
