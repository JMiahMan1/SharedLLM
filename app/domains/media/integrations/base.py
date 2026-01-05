# app/domains/media/integrations/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import asyncio
from app.settings import log

class MediaIntegration(ABC):
    """
    Base class for all media integrations.
    """
    service_type: str = "unknown"
    creates_wrapper: bool = False
    wrapper_detection: Optional[Dict[str, str]] = None
    unwrap_for_request_types: list = []
    
    @property
    @abstractmethod
    def integration_type(self) -> str:
        """Return the integration string (e.g., 'music_assistant', 'cast', 'roku')."""
        pass

    @abstractmethod
    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Execute play_media logic."""
        pass
        
    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Execute stop command."""
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, None)
        
    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Execute turn_on command."""
        raise NotImplementedError("turn_on must be implemented by subclass")

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Execute turn_off command."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "turn_off", entity_id, user_creds, {}, None)
        
    async def pause_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Pause media playback."""
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_pause", entity_id, user_creds, {}, None)
    
    async def play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Resume playback."""
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_play_pause", entity_id, user_creds, {}, None)
    
    async def volume_set(self, entity_id: str, volume: float, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Set volume level."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "volume_set", entity_id, user_creds, {"volume_level": volume}, None)

    async def volume_up(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Increase volume."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "volume_up", entity_id, user_creds, {}, None)

    async def volume_down(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Decrease volume."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "volume_down", entity_id, user_creds, {}, None)

    async def volume_mute(self, entity_id: str, is_volume_muted: bool, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Mute/unmute volume."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "volume_mute", entity_id, user_creds, {"is_volume_muted": is_volume_muted}, None)

    async def nav_home(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Navigate to Home screen."""
        from app.domains.shared import execute_ha_service
        domain = entity_id.split(".")[0]
        # For many TVs, stopping media or sending Home command via remote is best.
        # Base implementation does stop. Subclasses can use remote siblings.
        return await execute_ha_service(domain, "media_stop", entity_id, user_creds, {}, kwargs.get("redis_client"))

    async def open_app(self, entity_id: str, query: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Open a specific app."""
        from app.domains.shared import execute_ha_service
        from app.domains.media.integrations import APP_PACKAGES
        
        # Resolve package name
        package = None
        for name, pkg in APP_PACKAGES.items():
            if name in query.lower():
                package = pkg
                break
        
        if not package:
            return {"status": "FAILURE", "message": f"Could not determine app from: {query}"}
        
        # Ensure device is ON before selecting source (important for Android TV)
        try:
             # Just like play_media, we might need to wake it up
             # We can't call self.turn_on directly if it's not implemented, but we can try service call
             # or rely on subclass implementation if available.
             # Safest bet: call turn_on from this class or let HA handle it.
             # But base turn_on raises NotImplementedError.
             # Let's try sending "turn_on" service first.
             domain = entity_id.split(".")[0]
             await execute_ha_service(domain, "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))
             await asyncio.sleep(6) # Wait for wake (increased for slow Android TVs)
        except Exception as e:
             log.warning(f"[open_app] Auto-turn-on failed: {e}")

        return await execute_ha_service("media_player", "select_source", entity_id, user_creds, {"source": package}, kwargs.get("redis_client"))


async def unwrap_entity_if_needed(entity_id: str, request_type: str, user_creds: dict) -> str:
    """Unwrap wrapper entities."""
    try:
        import requests
        from app.settings import HA_URL
        headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}", "Content-Type": "application/json"}
        response = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        if response.status_code != 200: return entity_id
        entity_data = response.json()
        attributes = entity_data.get("attributes", {})
        from app.domains.media.integrations.factory import IntegrationFactory
        from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
        from app.domains.media.integrations.cast import CastIntegration
        integration_classes = [MusicAssistantIntegration, CastIntegration]
        for integration_class in integration_classes:
            if not integration_class.creates_wrapper: continue
            if not integration_class.wrapper_detection: continue
            wrapper_attr = integration_class.wrapper_detection.get("attribute")
            if wrapper_attr and attributes.get(wrapper_attr):
                if request_type in integration_class.unwrap_for_request_types:
                    underlying_attr = integration_class.wrapper_detection.get("underlying_device_attribute")
                    underlying_device = attributes.get(underlying_attr)
                    if underlying_device and "." in underlying_device:
                        log.info(f"[Generic Unwrap] {integration_class.__name__}: {entity_id} → {underlying_device}")
                        return underlying_device
        return entity_id
    except Exception as e:
        log.warning(f"[Generic Unwrap] Error: {e}")
        return entity_id

class VideoHelperMixin:
    """Mixin for video downloading and streaming."""
    async def _resolve_playlist_to_video(self, url: str) -> Optional[str]:
        try:
            import yt_dlp
            def extract():
                with yt_dlp.YoutubeDL({'extract_flat': 'in_playlist', 'playlistend': 1, 'quiet': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info['entries'][0]['id'] if 'entries' in info and info['entries'] else None
            return await asyncio.get_running_loop().run_in_executor(None, extract)
        except Exception: return None

    async def _search_and_filter_video_url(self, query: str) -> Optional[str]:
        try:
            from app.logic.web_search import tool_web_search
            import re
            search_results = await tool_web_search(f"{query} youtube")
            urls = re.findall(r'URL: (https?://[^\s]+)', search_results)
            for url in urls:
                if any(x in url for x in ["/channel/", "/user/", "/@"]): continue
                if "youtube.com" in url or "youtu.be" in url: return url
            return None
        except Exception: return None

    async def _download_and_serve_video(self, url: str) -> Optional[str]:
        try:
            from app.utils.video_cache import download_video_progressive, get_video_id
            from app.settings import SERVER_URL
            video_id = get_video_id(url)
            file_path, ready = await download_video_progressive(url, video_id)
            if not ready or not file_path: return None
            return f"{SERVER_URL}/cast_video/{file_path.name}"
        except Exception: return None

    async def _extract_direct_stream_url(self, url: str) -> str:
        try:
            import yt_dlp
            def run():
                with yt_dlp.YoutubeDL({'format': 'best[ext=mp4]/best', 'quiet': True}) as ydl:
                    return ydl.extract_info(url, download=False).get('url')
            return await asyncio.get_running_loop().run_in_executor(None, run)
        except Exception: return None
