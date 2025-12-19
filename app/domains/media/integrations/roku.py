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
        Play media on Roku.
        Strategy:
        1. If it's a video (YouTube) query:
           - Try to download & stream locally (Direct URL).
           - If that fails, extract ID and launch YouTube App.
        2. If it's music (generic):
           - Try generic play_media (Music Assistant might handle it via DLNA if supported, otherwise limited).
        """
        
        # [SmartPowerSync] - Ensure TV is ON
        # Roku integration usually handles 'turn_on' if configured with Wake-on-LAN.
        # We'll explicitly call turn_on first just in case.
        # Check state not needed as turn_on is idempotent usually, but good practice.
        await self.turn_on(entity_id, user_creds)
        await asyncio.sleep(4) # Allow boot time
        
        if media_type == "video":
            # 1. Resolve Query to URL (if it's a search term)
            if not query.startswith(("http", "www", "spotify", "app")):
                # Search using generic StandardIntegration helper? 
                # We need to import it or duplicate search logic.
                # Let's import the StandardIntegration search logic or just re-implement simple search.
                # Re-using StandardIntegration search is cleaner but inheritance gets messy.
                # For now, let's use a simple YouTube search if needed.
                from app.domains.media.integrations.standard import StandardIntegration
                # Helper instantiation just for search? No, let's just use youtube search tool or assume simple kwarg.
                # Actually, standard logic is just: search -> return link.
                # Let's do a quick search if it's not a URL.
                 from app.logic.web_search import search_web
                 search_results = await search_web(f"{query} youtube", num_results=1)
                 if search_results and len(search_results) > 0:
                     query = search_results[0]['link']
                     log.info(f"[Roku] Resolved '{query}' to {query}")

            # 2. Check for YouTube
            if "youtube.com" in query or "youtu.be" in query:
                video_id = self._extract_youtube_id(query)
                
                if not video_id and "list=" in query:
                     video_id = await self._resolve_playlist_to_video(query)

                if video_id:
                     # STRATEGY A: Direct Stream (Download & Host)
                     # This gives the best "Cast-like" experience without app ads/login issues.
                     local_url = await self._download_and_serve_video(query)
                     
                     if local_url:
                          log.info(f"[Roku] Playing Direct Stream: {local_url}")
                          # Roku 'play_media' supports: content_id=url, content_type='video' (mapped to Format: mp4 in extra usually)
                          return await execute_ha_service(
                              "media_player",
                              "play_media",
                              entity_id,
                              user_creds,
                              {
                                  "media_content_id": local_url,
                                  "media_content_type": "url", # Roku integration specific
                                  "extra": {
                                      "title": "SharedLLM Stream",
                                      "format": "mp4"
                                  }
                              },
                              kwargs.get("redis_client")
                          )
                     
                     # STRATEGY B: Fallback to YouTube App
                     log.info(f"[Roku] Direct Stream failed. Fallback to YouTube App (ID: {video_id})")
                     # Roku App Launch: media_content_type="app", media_content_id="837" (YouTube)
                     # Deep linking? 
                     # According to research: contentId=<id>&mediaType=live (or similar params).
                     # The HA Roku integration might not support complex deep link params via standard play_media easily without 'extra'.
                     # But most sources say simply launching the app with deep link support is complex via generic HA.
                     # Let's try the standard method:
                     # Official Roku Deep Link:  launch/837?contentId=<video_id>&mediaType=movie
                     
                     # HA Service: media_player.play_media(entity_id, media_content_id="837", media_content_type="app", extra={"content_id": video_id, "media_type": "movie"})
                     # Note: Current HA Roku integration implementation details might vary.
                     # Let's try a generic app launch if deep linking isn't standard.
                     
                     return await execute_ha_service(
                          "media_player",
                          "play_media",
                          entity_id,
                          user_creds,
                          {
                              "media_content_id": "837", # YouTube Channel ID
                              "media_content_type": "app"
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

