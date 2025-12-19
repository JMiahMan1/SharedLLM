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

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Roku via Direct Casting (User Requirement).
        """
        log.info(f"[Roku] Playing Media: {query} (Entity: {entity_id}) - CAST MODE")
        
        # [SmartPowerSync] - Ensure TV is ON and Ready
        await self.turn_on(entity_id, user_creds)
        
        # Poll for state change (timeout 20s) instead of blind sleep
        for i in range(10):
            state = await self.get_state(entity_id, user_creds)
            if state and state.state in ["on", "idle", "home", "playing", "paused"]:
                log.info(f"[Roku] Device is verified ON (State: {state.state})")
                break
            if i % 2 == 0: # Retry turn_on every 4s if still off
                 await self.turn_on(entity_id, user_creds)
            await asyncio.sleep(2)
        else:
            log.warning("[Roku] Device did not report ON state after wait. Proceeding but command may fail.")
        
        if media_type == "video":
            # 1. Resolve Query
            if not query.startswith(("http", "www", "spotify", "app")):
                from app.logic.web_search import search_web
                search_results = await search_web(f"{query} youtube", num_results=1)
                if search_results and len(search_results) > 0:
                    query = search_results[0]['link']
                    log.info(f"[Roku] Resolved '{query}' to {query}")

            # 2. Download & Cast (Direct Stream)
            # This is mandated by user ("HAVE to do the cast feature").
            local_url = await self._download_and_serve_video(query)
            
            if local_url:
                 log.info(f"[Roku] Streaming: {local_url}")
                 return await execute_ha_service(
                     "media_player",
                     "play_media",
                     entity_id,
                     user_creds,
                     {
                         "media_content_id": local_url,
                         "media_content_type": "video", # REQUIRED: 'video', not 'url'
                         "extra": {
                             "title": "SharedLLM Stream",
                             "format": "mp4" # REQUIRED
                         }
                     },
                     kwargs.get("redis_client")
                 )
            else:
                log.error("[Roku] Failed to generate local stream URL.")

        # Fallback: Generic
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

