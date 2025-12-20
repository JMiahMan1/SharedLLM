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
        
        # 1. Get Roku IP
        roku_ip = await self._get_roku_ip(entity_id, user_creds)
        if not roku_ip:
            return {"status": "FAILURE", "message": "Roku IP address not found"}

        # 2. Prepare Common Params
        params = {}
        
        # 3. Handle Types
        if media_type == "music":
            # Music Logic
            params["t"] = "a"
            params["u"] = query 
            
            # Extract Metadata from kwargs (passed from MA or inferred)
            # MA usually passes metadata in kwargs or we can fetch if needed
            if kwargs.get("media_title"):
                params["songName"] = kwargs.get("media_title")
            if kwargs.get("media_artist"):
                params["artistName"] = kwargs.get("media_artist")
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
                # Clean query using same logic as standard
                cleaned_query = std_integration._clean_query(query, media_type, entity_id, kwargs.get("device_name"))
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
            params["videoFormat"] = "mp4" # Force mp4 as we use yt-dlp to serve mp4
            
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
