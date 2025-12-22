# app/domains/media/integrations/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.settings import log

class MediaIntegration(ABC):
    """
    Base class for all media integrations.
    
    Service Registry Pattern Metadata:
    - service_type: Type of media service (e.g., "music", "video", "audiobook")
    - creates_wrapper: Whether integration creates wrapper entities  
    - wrapper_detection: Dict with detection attributes
    - unwrap_for_request_types: List of request types requiring unwrap
    """
    service_type: str = "unknown"
    creates_wrapper: bool = False  # Does this integration create wrapper entities?
    wrapper_detection: Optional[Dict[str, str]] = None  # {"attribute": "mass_player_type", "underlying_device_attribute": "active_queue"}
    unwrap_for_request_types: list = []  # ["video", "transport", "music", etc.]
    
    @property
    @abstractmethod
    def integration_type(self) -> str:
        """Return the integration string (e.g., 'music_assistant', 'cast', 'roku')."""
        pass

    @abstractmethod
    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute play_media logic for this integration.
        Responsible for payload formation, query cleaning, and service calls.
        """
        pass
        
    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute stop command. Can be overridden for specific logic.
        Default: Call media_player.media_stop
        """
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, None)
        
    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute turn_on command.
        """
        raise NotImplementedError("turn_on must be implemented by subclass")
        
    async def pause_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Pause media playback.
        Default: Call media_player.media_pause
        """
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_pause", entity_id, user_creds, {}, None)
    
    async def play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Resume/play media playback.
        Default: Call media_player.media_play_pause (toggles play/pause)
        """
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_play_pause", entity_id, user_creds, {}, None)


async def unwrap_entity_if_needed(
    entity_id: str,
    request_type: str,  # "music", "video", "transport"
    user_creds: dict
) -> str:
    """
    Generic wrapper unwrap logic based on integration metadata.
    Returns the underlying device entity_id if unwrap is needed, else returns original.   
    Uses Service Registry pattern: reads integration metadata instead of hardcoded checks.
    """
    try:
        # Get full entity state
        import requests
        from app.settings import HA_URL
        headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}", "Content-Type": "application/json"}
        response = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        
        if response.status_code != 200:
            log.warning(f"[Generic Unwrap] Failed to fetch entity state for {entity_id}")
            return entity_id
        
        entity_data = response.json()
        attributes = entity_data.get("attributes", {})
        
        # Check all integrations for wrapper detection
        from app.domains.media.integrations.factory import IntegrationFactory
        from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
        from app.domains.media.integrations.cast import CastIntegration
        
        # Get all integration classes (manually for now - factory doesn't expose this yet)
        integration_classes = [MusicAssistantIntegration, CastIntegration]
        
        for integration_class in integration_classes:
            if not integration_class.creates_wrapper:
                continue
            
            # Check if entity matches this wrapper type
            if not integration_class.wrapper_detection:
                continue
                
            wrapper_attr = integration_class.wrapper_detection.get("attribute")
            if wrapper_attr and attributes.get(wrapper_attr):
                # This is a wrapper from this integration
                
                #Should we unwrap for this request type?
                if request_type in integration_class.unwrap_for_request_types:
                    underlying_attr = integration_class.wrapper_detection.get("underlying_device_attribute")
                    underlying_device = attributes.get(underlying_attr)
                    
                    if underlying_device:
                        log.info(f"[Generic Unwrap] {integration_class.__name__}: {entity_id} → {underlying_device} (request_type={request_type})")
                        return underlying_device
                    else:
                        log.warning(f"[Generic Unwrap] {integration_class.__name__} wrapper detected but no underlying device found")
                else:
                    log.info(f"[Generic Unwrap] Keeping {integration_class.__name__} wrapper {entity_id} for request_type={request_type}")
                    return entity_id
        
        # No wrapper detected or unwrap not needed
        return entity_id
        
    except Exception as e:
        log.warning(f"[Generic Unwrap] Error: {e}")
        return entity_id


class VideoHelperMixin:
    """
    Mixin for video downloading and streaming logic (yt-dlp).
    """

    async def _resolve_playlist_to_video(self, url: str) -> Optional[str]:
        """Resolves a playlist URL to the first video ID."""
        try:
            import yt_dlp
            import asyncio
            
            def extract_playlist_first_video():
                ydl_opts = {
                    'extract_flat': 'in_playlist', # Just get metadata, don't download
                    'playlistend': 1, # Only get first item
                    'quiet': True,
                    'no_warnings': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'entries' in info and len(info['entries']) > 0:
                        return info['entries'][0]['id']
                    return None

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, extract_playlist_first_video)
        except Exception as e:
            log.warning(f"[VideoHelper] Failed to resolve playlist: {e}")
            return None

    async def _search_and_filter_video_url(self, query: str) -> Optional[str]:
        """
        Search for a video URL and filter out non-playable pages (channels, users).
        Returns the first valid video URL or None.
        """
        try:
            from app.logic.web_search import tool_web_search
            import re
            
            log.info(f"[VideoHelper] Searching for: {query} youtube")
            search_results = await tool_web_search(f"{query} youtube")
            urls = re.findall(r'URL: (https?://[^\s]+)', search_results)
            
            for url in urls:
                # Skip channel/user pages (not playable)
                if any(x in url for x in ["/channel/", "/user/", "/@"]):
                    log.info(f"[VideoHelper] Skipping Channel URL: {url}")
                    continue
                
                # Prioritize actual video URLs
                if "youtube.com/watch?v=" in url or "youtu.be/" in url:
                    log.info(f"[VideoHelper] Found video: {url}")
                    return url
                
                # Fallback to other YouTube URLs (shorts, embed, etc.)
                if "youtube.com" in url or "youtu.be" in url:
                    log.info(f"[VideoHelper] Found fallback URL: {url}")
                    return url
            
            log.warning(f"[VideoHelper] No valid video URL found for: {query}")
            return None
            
        except Exception as e:
            log.error(f"[VideoHelper] Search error: {e}")
            return None

    def _extract_youtube_id(self, url: str) -> str:
        """Extracts video ID from various YouTube URL formats."""
        import re
        patterns = [
            r'(?:v=|\/)([\w-]{11})(?:\?|&|\/|$)', # v=ID or /ID
            r'youtu\.be\/([\w-]{11})',             # youtu.be/ID
            r'embed\/([\w-]{11})'                  # embed/ID
        ]
        
        for p in patterns:
            match = re.search(p, url)
            if match:
                return match.group(1)
        return None

    async def _download_and_serve_video(self, url: str) -> Optional[str]:
        """
        Download video locally and return HTTP URL for streaming.
        Uses progressive download - returns as soon as initial buffer is ready.
        """
        try:
            from app.utils.video_cache import download_video_progressive, get_video_id
            
            # Get unique video ID
            video_id = get_video_id(url)
            log.info(f"[VideoHelper] Starting progressive download for video {video_id}")
            
            # Download with initial buffer
            file_path, ready = await download_video_progressive(url, video_id)
            
            if not ready or not file_path:
                log.error(f"[VideoHelper] Progressive download failed for {url}")
                return None
            
            # Return local streaming URL
            # Using server's external IP so devices can access it
            from app.settings import SERVER_URL
            local_url = f"{SERVER_URL}/cast_video/{video_id}.mp4"
            log.info(f"[VideoHelper] Video ready at: {local_url}")
            
            return local_url
            
        except Exception as e:
            log.error(f"[VideoHelper] Download and serve error: {e}")
            return None

    async def _extract_direct_stream_url(self, url: str) -> str:
        """Attempts to extract a direct mp4 stream using yt-dlp."""
        try:
            import yt_dlp
            import asyncio
            
            log.info("[VideoHelper] Attempting yt-dlp extraction...")
            
            # Run in executor to avoid blocking loop
            loop = asyncio.get_running_loop()
            
            def run_extraction():
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get('url')

            return await loop.run_in_executor(None, run_extraction)
            
        except ImportError:
            log.warning("[VideoHelper] yt-dlp not installed. Skipping direct stream extraction.")
        except Exception as e:
            log.warning(f"[VideoHelper] yt-dlp extraction failed: {e}")
        
        return None
