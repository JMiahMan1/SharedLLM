from typing import Dict, Any, Optional
import logging
import asyncio
import requests
from app.domains.media.integrations.base import MediaIntegration, VideoHelperMixin
from app.domains.shared import execute_ha_service
from app.settings import log

class RokuMediaAssistantIntegration(MediaIntegration, VideoHelperMixin):
    """
    Roku TV Integration using MedievalApple/Media-Assistant (Channel 782875).
    
    This integration replaces the standard Roku integration when enabled.
    It utilizes the Media-Assistant channel for:
    - Rich Music UI (Album Art, Artist, Song Name)
    - Direct Video Playback
    """
    
    service_type = "media_assistant"
    creates_wrapper = False 
    
    # Constants
    MEDIA_ASSISTANT_CHANNEL_ID = "782875" # Store Version
    MEDIA_ASSISTANT_DEV_ID = "dev"
    
    @property
    def integration_type(self) -> str:
        return "roku_media_assistant"
    
    async def get_state(self, entity_id: str, user_creds: Dict) -> Optional[Any]:
        """Get current state of the entity"""
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
            log.warning(f"[RokuMA] Failed to get state: {e}")
            return None

    async def _get_roku_ip(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """Get Roku IP address using SSDP network discovery (Copied from RokuIntegration for isolation)"""
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
                    log.info(f"[RokuMA] Discovered IP: {ip}")
                    return ip
                else:
                    log.error(f"[RokuMA] SSDP discovery found no Roku devices for {entity_id}")
                    return None
        except Exception as e:
            log.error(f"[RokuMA] Discovery error: {e}")
            return None

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media using Media-Assistant.
        Supports:
        - Music: t=a, with metadata
        - Video: t=v, with title
        """
        log.info(f"[RokuMA] Playing Media: {query} (Entity: {entity_id}) - Type: {media_type}")
        
        # 2. SmartPowerSync - Ensure TV is ON and Awake
        # Roku 'idle' often means screensaver or standby. We must force wake.
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        # Check current state
        initial_state = await self.get_state(entity_id, user_creds)
        current_state_value = initial_state.state if initial_state else "unknown"
        
        log.info(f"[RokuMA] Smart Power Check: State is '{current_state_value}'")
        
        # If OFF or IDLE, wake it up
        if current_state_value in ["off", "idle", "unavailable", "unknown"]:
            log.info(f"[RokuMA] Device is {current_state_value}, waking up...")
            
            # 1. Turn On Service
            await self.turn_on(entity_id, user_creds)
            await asyncio.sleep(2)
            
            # 2. Send Home Key (Wakes display from screensaver/eco mode)
            log.info(f"[RokuMA] Sending Home button to force display wake...")
            await execute_ha_service("remote", "send_command", remote_entity_id, user_creds, {"command": "Home"}, kwargs.get("redis_client"))
            await asyncio.sleep(2)
            
            # 3. Verify (Optional logging)
            new_state = await self.get_state(entity_id, user_creds)
            log.info(f"[RokuMA] Post-Wake State: {new_state.state if new_state else 'None'}")

        # 3. Prepare Common Params
        params = {}
        
        # 4. Get Roku IP (After wake, to ensure networking is active)
        roku_ip = await self._get_roku_ip(entity_id, user_creds)
        if not roku_ip:
            return {"status": "FAILURE", "message": "Roku IP address not found"}

        # 5. Handle Types
        if media_type == "music":
            # Music Logic - Clean query first
            from app.domains.media.integrations.standard import StandardIntegration
            std_integration = StandardIntegration()
            
            # Extract friendly_name from metadata for query cleaning
            device_name = kwargs.get("friendly_name")
            
            # Clean the query to remove device names and action words
            cleaned_query = std_integration._clean_query(query, media_type, entity_id, device_name)
            log.info(f"[RokuMA Music] Cleaned query: '{cleaned_query}' (from '{query}')")
            
            # "Listen" intent requires Audio mode (t=a)
            # App expects query in 'u'.
            params["t"] = "a"
            params["u"] = cleaned_query 
            
            # Extract Metadata from kwargs (passed from MA or inferred)
            # MA usually passes metadata in kwargs or we can fetch if needed
            
            # FORCE SongName to be the Cleaned Query
            # This avoids "Unknown song" error (missing metadata) AND "Full Query" error (dirty metadata from Intent)
            params["songName"] = cleaned_query
            
            if kwargs.get("media_artist"):
                params["artistName"] = kwargs.get("media_artist")
            else:
                 # Fallback: Put query in artist too if missing
                 params["artistName"] = cleaned_query
            if kwargs.get("media_album_name"):
                params["albumName"] = kwargs.get("media_album_name")
            if kwargs.get("image_url"):
                params["albumArt"] = kwargs.get("image_url")

            log.info(f"[RokuMA] Music Params: {params}")

        elif media_type == "video":
            # Video Logic
            
            
            # Resolve Query if needed (same as StandardIntegration)
            if not query.startswith(("http", "www", "spotify", "app")):
                from app.domains.media.integrations.standard import StandardIntegration
                std_integration = StandardIntegration()
                
                # Extract friendly_name from metadata for query cleaning
                device_name = kwargs.get("friendly_name")  # Metadata is unpacked into kwargs
                log.info(f"[RokuMA] Extracting device_name for query cleaning: {device_name}")
                
                # Clean query using same logic as standard
                cleaned_query = std_integration._clean_query(query, media_type, entity_id, device_name)
                resolved_url = await std_integration._search_video_url(cleaned_query)
                if resolved_url:
                    query = resolved_url
                else:
                     return {"status": "FAILURE", "message": "Could not find a playable video"}

            # Convert to local stream if needed (yt-dlp)
            # We assume query is now a URL.
            # Use VideoHelperMixin to process it
            local_url = await self._download_and_serve_video(query)
            
            if not local_url:
                 return {"status": "FAILURE", "message": "Failed to prepare video stream"}
            
            params["t"] = "v"
            params["u"] = local_url
            params["videoName"] = kwargs.get("media_title", "Video")
            params["videoFormat"] = "hls" if "m3u8" in local_url else "mp4"
            
            log.info(f"[RokuMA] Video Params: {params}")

        else:
            log.warning(f"[RokuMA] Unsupported media type: {media_type}, defaulting to video")
            params["t"] = "v"
            params["u"] = query

        # 4. Launch Media-Assistant
        # Using /launch to force app open and handle args
        # ECP URL: http://IP:8060/launch/782875?args...
        
        base_url = f"http://{roku_ip}:8060/launch/{self.MEDIA_ASSISTANT_CHANNEL_ID}"
        
        try:
            log.info(f"[RokuMA] Sending ECP request to {base_url}")
            resp = requests.post(base_url, params=params, timeout=10)
            
            if resp.status_code == 200:
                # Store last video URL if this was a video playback
                if media_type == "video":
                    redis_client = kwargs.get("redis_client")
                    if redis_client:
                        user = user_creds.get("user", "admin")
                        last_video_key = f"roku_last_video:{entity_id}:{user}"
                        # Store the original query (before local stream conversion)
                        original_query = kwargs.get("original_query", query)
                        redis_client.setex(last_video_key, 3600, original_query)  # 1 hour TTL
                        log.info(f"[RokuMA] Stored last video URL for resume: {original_query}")
                
                return {
                     "status": "SUCCESS",
                     "message": f"Launched Media-Assistant on {entity_id}",
                     "entity_id": entity_id,
                     "service": "roku_ma_launch"
                 }
            else:
                return {
                     "status": "FAILURE", 
                     "message": f"Roku returned {resp.status_code}",
                     "entity_id": entity_id
                 }
                 
        except Exception as e:
            log.error(f"[RokuMA] Request failed: {e}")
            return {"status": "FAILURE", "message": f"Exception: {e}"}

    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Stop media by sending Home command (Exits app).
        """
        # Logic is identical to standard Roku - send Home key
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        return await execute_ha_service(
            "remote", "send_command", remote_entity_id, user_creds, {"command": "Home"}, kwargs.get("redis_client")
        )

    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Turn on Roku device"""
        log.info(f"[RokuMA] Turning on {entity_id}")  
        return await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Turn off Roku - send explicit PowerOff command"""
        from app.domains.media.devices import get_entity_state
        
        log.info(f"[RokuMA] Turning off {entity_id}")
        
        # Get remote entity
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        if not remote_entity_id:
             # Fallback if no remote found (unlikely)
             remote_entity_id = entity_id.replace("media_player.", "remote.")

        # Send PowerOff button
        # This is more effective for Roku TVs than just Home (which only exits apps)
        return await execute_ha_service(
            "remote", 
            "send_command", 
            remote_entity_id, 
            user_creds, 
            {"command": "PowerOff"}, 
            kwargs.get("redis_client")
        )

    async def _get_roku_remote(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """
        Find the remote entity for this Roku device by checking the same group.
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
                                    log.info(f"[RokuMA] Found remote entity via group: {remote_entity}")
                                    return remote_entity
        except Exception as e:
            log.warning(f"[RokuMA] ChromaDB lookup failed: {e}")
        
        # Strategy 2: Fallback to simple replacement
        if not remote_entity:
            remote_entity = entity_id.replace("media_player.", "remote.")
            log.info(f"[RokuMA] Using fallback remote entity: {remote_entity}")
        
        return remote_entity

    async def play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Resume/play for Roku.
        - For music: Use Play button toggle (works with Music Assistant)
        - For video: If device is 'off' (app exited), re-launch last video via Media Assistant
        """
        from app.domains.media.devices import get_entity_state
        
        log.info(f"[RokuMA] Resuming playback on {entity_id}")
        
        # Check current state
        state = await get_entity_state(entity_id, user_creds)
        log.info(f"[RokuMA] Current state: {state}")
        
        # If device is 'off', video app has exited - need to re-launch
        if state == "off":
            log.info(f"[RokuMA] Device is off (video app exited). Checking for last video to re-launch...")
            
            # Check if we have a last video URL stored
            redis_client = kwargs.get("redis_client")
            if redis_client:
                # Try to get last video from Redis
                user = user_creds.get("user", "admin")
                last_video_key = f"roku_last_video:{entity_id}:{user}"
                
                try:
                    last_video_url = redis_client.get(last_video_key)
                    if last_video_url:
                        log.info(f"[RokuMA] Re-launching last video: {last_video_url}")
                        # Re-launch using play_media
                        return await self.play_media(
                            entity_id,
                            last_video_url.decode() if isinstance(last_video_url, bytes) else last_video_url,
                            "video",
                            user_creds,
                            **kwargs
                        )
                except Exception as e:
                    log.warning(f"[RokuMA] Failed to get last video: {e}")
            
            # If no last video, fall back to Play button (may not work)
            log.warning(f"[RokuMA] No last video found, attempting Play button (may fail)")
        
        # For music or if device is not off, use Play button toggle
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play button
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},
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
        """Pause using Play button toggle"""
        log.info(f"[RokuMA] Pausing playback on {entity_id}")
        
        # Get remote entity from same group
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play button (toggles)
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},
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

