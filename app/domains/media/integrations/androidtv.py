from typing import Dict, Any
import logging
import asyncio
from app.domains.media.integrations.standard import StandardIntegration
from app.domains.media.integrations.base import VideoHelperMixin
from app.domains.shared import execute_ha_service

log = logging.getLogger(__name__)

class AndroidTVIntegration(StandardIntegration, VideoHelperMixin):
    """
    Android TV Integration.
    Prioritizes local downloading and casting of YouTube videos (via yt-dlp)
    instead of opening the YouTube app, to ensure consistent playback independent of user state.
    """
    
    @property
    def integration_type(self) -> str:
        return "androidtv"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Android TV.
        Intercepts YouTube requests to download and cast the file locally.
        """
        # [Generic Wrapper Unwrap]
        from app.domains.media.integrations.base import unwrap_entity_if_needed
        log.info(f"[AndroidTV] Initial entity_id: {entity_id}")
        entity_id = await unwrap_entity_if_needed(entity_id, media_type, user_creds)
        log.info(f"[AndroidTV] Resolved entity_id: {entity_id}")
        
        # [Music Delegation]
        if media_type == "music":
             # Use StandardIntegration's logic for MA check
             return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

        redis_client = kwargs.get("redis_client")

        # [Auto-Power On]
        await self.turn_on(entity_id, user_creds, **kwargs)
        # Wait slightly less than Standard because we need to process the download anyway
        # But if we await download, that might be enough wait time.
        
        # [Video Logic]
        if media_type == "video":
            # 1. Clean Query
            cleaned_query = self._clean_query(query, media_type, entity_id, kwargs.get("device_name"))
            
            # 2. Search if not a URL
            found_url = None
            if not query.startswith(("http", "www", "spotify", "app")):
                 found_url = await self._search_video_url(cleaned_query)
                 if found_url:
                     cleaned_query = found_url # Use the URL
            else:
                 found_url = cleaned_query # It's already a URL

            # 3. Check for YouTube
            if found_url and ("youtube.com" in found_url or "youtu.be" in found_url):
                 log.info(f"[AndroidTV] YouTube detected. Intercepting for local download & cast: {found_url}")
                 
                 # Download video locally and serve via HTTP for stable Cast streaming
                 # This mimics the CastIntegration behavior requested by the user.
                 local_url = await self._download_and_serve_video(found_url)
                 
                 if local_url:
                     log.info(f"[AndroidTV] Video ready for streaming at: {local_url}")
                     
                     # [Cast Delegation]
                     # Android TV entities often struggle with raw URL playback via ADB/HA.
                     # We should delegate this to the Cast sibling (e.g. _chrome) if available.
                     target_entity = entity_id
                     try:
                         from app.domains.media.devices import find_group_sibling
                         def is_cast(meta):
                             # Must be 'cast' integration
                             if "cast" not in meta.get("integration", "").lower():
                                 return False
                             
                             # Must NOT be Music Assistant (MA often wraps Cast devices)
                             # Check attributes for 'mass_player_type' or 'music_assistant'
                             attrs = meta.get("attributes", {})
                             attrs_str = str(attrs)
                             if "mass_player_type" in attrs_str or "music_assistant" in attrs_str:
                                 return False
                                 
                             return True
                         
                         sibling = await find_group_sibling(entity_id, is_cast)
                         if sibling:
                             log.info(f"[AndroidTV] Delegating video playback to Cast sibling (Raw/Non-MA): {sibling}")
                             target_entity = sibling
                     except Exception as e:
                         log.warning(f"[AndroidTV] Failed to find cast sibling: {e}")

                     payload = {
                         "media_content_id": local_url,
                         "media_content_type": "video/mp4"  # Use specific mime type for better compatibility
                     }
                     # Explicitly stop the target first to clear any MA session overlay
                     try:
                         await execute_ha_service("media_player", "media_stop", target_entity, user_creds, {}, redis_client)
                         await asyncio.sleep(1)
                     except: pass

                     log.info(f"[AndroidTV] Sending payload: {payload} to {target_entity}")
                     return await execute_ha_service(
                         "media_player", 
                         "play_media", 
                         target_entity, 
                         user_creds, 
                         payload, 
                         redis_client
                     )
                 else:
                     error_msg = "[AndroidTV] Download failed for local streaming. Aborting to prevent opening YouTube app."
                     log.warning(error_msg)
                     return {"status": "FAILURE", "message": error_msg}

        # Fallback to Standard Playback (e.g. non-YouTube video, or music)
        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

    async def open_app(self, entity_id: str, query: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Open a specific app on Android TV."""
        from app.domains.media.integrations import APP_PACKAGES
        
        # Resolve package name
        package = None
        for name, pkg in APP_PACKAGES.items():
             if name in query.lower():
                 package = pkg
                 break
        
        if not package:
             return {"status": "FAILURE", "message": f"Could not determine app from: {query}"}

        # Auto-Power On
        await self.turn_on(entity_id, user_creds, **kwargs)
        # Wait for boot
        await asyncio.sleep(6) 
        
        log.info(f"[AndroidTV] Launching app {package} on {entity_id}")
        
        # 1. Try launching by Package ID (Standard Android TV Remote method)
        # Type: app, ID: com.package.name
        log.info(f"[AndroidTV] Attempting Launch via Package ID: {package}")
        await execute_ha_service(
             "media_player", 
             "play_media", 
             entity_id, 
             user_creds, 
             {
                 "media_content_id": package, 
                 "media_content_type": "app"
             }, 
             kwargs.get("redis_client")
        )

        # 2. [Robustness Retry]
        # Android TV Remote can be flaky if the device is waking up.
        # Send the "Launch App" command AGAIN after a short delay to ensure it registers.
        # This is strictly complying with "Just launch the app" (no links).
        await asyncio.sleep(3)
        log.info(f"[AndroidTV] Resending Launch Command for reliability: {package}")
        res = await execute_ha_service(
             "media_player", 
             "play_media", 
             entity_id, 
             user_creds, 
             {
                 "media_content_id": package, 
                 "media_content_type": "app"
             }, 
             kwargs.get("redis_client")
        )

        return res
