from typing import Dict, Any, Optional
import logging
import asyncio
import re
from app.domains.media.integrations.standard import StandardIntegration
from app.domains.media.integrations.base import VideoHelperMixin
from app.domains.shared import execute_ha_service

log = logging.getLogger(__name__)

class CastIntegration(StandardIntegration, VideoHelperMixin):
    """
    Google Cast Integration.
    Adds SmartPowerSync to ensure the physical TV is ON before playing on the Cast device.
    """
    
    # Service Registry Metadata
    service_type = "video"
    creates_wrapper = False  # Cast doesn't create wrapper entities
    
    @property
    def integration_type(self) -> str:
        return "cast"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Cast device, ensuring TV sibling is powered on.
        """
        # [Generic Wrapper Unwrap - Service Registry Pattern]
        # Use generic unwrap function instead of hardcoded MASS checks
        from app.domains.media.integrations.base import unwrap_entity_if_needed
        entity_id = await unwrap_entity_if_needed(entity_id, media_type, user_creds)
        
        # [Music Delegation] If music request AND entity is MA wrapper, delegate to MusicAssistantIntegration
        if media_type == "music":
            from app.settings import GlobalResources
            try:
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if docs and docs.get("metadatas"):
                    import json
                    attrs_str = docs["metadatas"][0].get("attributes", "{}")
                    attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                    
                    if attrs.get("mass_player_type"):
                        log.info(f"[Cast] Music request on MA wrapper, delegating to MusicAssistantIntegration")
                        from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
                        ma_integration = MusicAssistantIntegration()
                        return await ma_integration.play_media(entity_id, query, media_type, user_creds, **kwargs)
            except Exception as e:
                log.warning(f"[Cast] Failed to check MA wrapper status: {e}, continuing with standard play")
        
        # [Session Clearing for Video Playback]

        # If device is playing (e.g., Music Assistant session), stop it first
        # to prevent session conflicts when switching to video
        if media_type == "video":
            from app.domains.media.devices import get_entity_state
            current_state = await get_entity_state(entity_id, user_creds)
            if current_state in ["playing", "paused", "buffering"]:
                log.info(f"[Session Clear] Device {entity_id} is {current_state}. Stopping before video playback.")
                await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, None)
                await asyncio.sleep(1)  # Brief wait for stop to complete
        
        # [SmartPowerSync]
        await self._ensure_tv_on(entity_id, user_creds)

        # [Auto-Search for Cast]
        # We must resolve the URL *here* so we can check if it's YouTube.
        # Otherwise StandardIntegration does it too late.
        if media_type == "video" and not query.startswith(("http", "www", "spotify", "app")):
             # Use the search logic from base class (we can call it directly)
             # Note: We need to clean query locally first or rely on _search_video_url internal behavior?
             # _search_video_url takes raw query? checking standard.py... 
             # standard.py calls _clean_query inside play_media, but _search_video_url takes 'search_query'.
             # Let's clean it here to match behavior.
             cleaned = self._clean_query(query, media_type, entity_id, kwargs.get("device_name"))
             found_url = await self._search_video_url(cleaned)
             if found_url:
                 query = found_url # effective update
                 log.info(f"[CastIntegration] Pre-resolved video query to: {query}")
        
        # [YouTube Handling for Cast]
        # Chromecast typically cannot play raw YouTube URLs via 'video' type.
        # We must invoke the YouTube App (AppID: 233637DE) with the Video ID.
        if media_type == "video" and ("youtube.com" in query or "youtu.be" in query):
             video_id = self._extract_youtube_id(query)
             
             # If no video ID found but it's a playlist, resolve it
             if not video_id and "list=" in query:
                 log.info("[CastIntegration] Detected playlist, resolving first video...")
                 video_id = await self._resolve_playlist_to_video(query)
                 
             if video_id:
                 import json
                 
                 # Toggle Strategy: Set True to use yt-dlp (Bypass), False to use App (Login)
                 # True: Direct URL casting (no app launch, but may have extraction issues)
                 # False: YouTube App launch (stable but shows login prompts)
                 BYPASS_YOUTUBE_APP = True
                 
                 # [Strategy 1: Direct Stream Extraction (Bypass App/Login)]
                 if BYPASS_YOUTUBE_APP:
                     # Download video locally and serve via HTTP for stable Cast streaming
                     local_url = await self._download_and_serve_video(query)
                     if local_url:
                         log.info(f"[CastIntegration] Video ready for streaming at: {local_url}")
                         return await execute_ha_service(
                             "media_player", 
                             "play_media", 
                             entity_id, 
                             user_creds, 
                             {
                                 "media_content_id": local_url,
                                 "media_content_type": "video" 
                             }, 
                             kwargs.get("redis_client")
                         )

                 # [Strategy 2: Cast App (Fallback/Default)]
                 log.info(f"[CastIntegration] Launching YouTube App for Video ID: {video_id}")
                 
                 # Prepare "Cast" payload
                 # Using 'app_name': 'youtube' triggers HA's internal YouTube controller
                 cast_payload = {
                     "app_name": "youtube",
                     "media_id": video_id
                 }
                 
                 return await execute_ha_service(
                     "media_player", 
                     "play_media", 
                     entity_id, 
                     user_creds, 
                     {
                         "media_content_id": json.dumps(cast_payload),
                         "media_content_type": "cast"
                     }, 
                     kwargs.get("redis_client")
                 )

        # Proceed with Standard Playback
        # If we updated 'query' to a URL, super() will skip search and just play it.
        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

    async def _ensure_tv_on(self, entity_id: str, user_creds: Dict):
        """
        Finds the physical TV sibling for this Cast device and turns it on if needed.
        
        NOTE: This SmartPowerSync logic is designed for Android TV integration.
        Other TV platforms (Roku, Samsung, LG/WebOS) may have different:
        - Entity structures
        - Integration names ("roku", "samsungtv", "webostv" vs "androidtv")
        - Device grouping patterns
        
        The device_class="tv" check works reliably for Android TV but may need
        platform-specific adjustments for other TV types.
        """
        try:
            from app.settings import GlobalResources
            from app.domains.media.devices import get_entity_state
            
            tv_sibling = None
            
            # Strategy 1: ChromaDB group lookup
            try:
                if GlobalResources.ha_collection:
                    current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    if current_docs and current_docs.get("metadatas"):
                        current_group_id = current_docs["metadatas"][0].get("group_id")
                        
                        if current_group_id and current_group_id != "unknown":
                            log.info(f"[SmartPowerSync] Searching for TV in group {current_group_id}")
                            
                            # Find all devices in same group
                            group_docs = GlobalResources.ha_collection._collection.get(
                                where={"group_id": current_group_id},
                                include=["metadatas"]
                            )
                            
                            if group_docs and group_docs.get("metadatas"):
                                for metadata in group_docs["metadatas"]:
                                    candidate_id = metadata.get("entity_id")
                                    candidate_integration = metadata.get("integration", "")
                                    
                                    # Parse attributes to check device_class
                                    attrs_str = metadata.get("attributes", "{}")
                                    try:
                                        import json
                                        attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                                        device_class = attrs.get("device_class")
                                    except:
                                        device_class = None
                                    
                                    # Find actual TV device (device_class == "tv"), not Cast or MA devices
                                    if (device_class == "tv" and 
                                        candidate_integration != "music_assistant" and
                                        candidate_id != entity_id):
                                        tv_sibling = candidate_id
                                        log.info(f"[SmartPowerSync] Found TV sibling via group: {tv_sibling}")
                                        break
            except Exception as e:
                log.warning(f"[SmartPowerSync] ChromaDB lookup failed: {e}")
            
            # Strategy 2: Fallback to suffix stripping
            if not tv_sibling:
                # Common suffixes for cast devices of TVs
                base = entity_id
                for suffix in ["_chrome_2", "_chrome", "_cast", "_speaker"]:
                    base = base.replace(suffix, "")
                
                if base != entity_id:
                     tv_sibling = base
                     log.info(f"[SmartPowerSync] Found TV sibling via suffix stripping: {tv_sibling}")
            
            if tv_sibling:
                try:
                    tv_state = await get_entity_state(tv_sibling, user_creds)
                    if tv_state in ["off", "standby", "unavailable", "unknown"]:
                        log.info(f"[SmartPowerSync] TV {tv_sibling} is {tv_state}. Turning ON.")
                        await execute_ha_service("media_player", "turn_on", tv_sibling, user_creds, {}, None)
                        await asyncio.sleep(4)  # Wait for TV boot
                    else:
                        log.info(f"[SmartPowerSync] TV {tv_sibling} is already {tv_state}")
                except Exception as e:
                     log.warning(f"[SmartPowerSync] Failed to power on {tv_sibling}: {e}")
            else:
                log.warning(f"[SmartPowerSync] No TV sibling found for {entity_id}")
                
        except Exception as e:
            log.warning(f"[SmartPowerSync] Error: {e}")
