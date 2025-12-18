from typing import Dict, Any
import logging
import asyncio
import re
from app.domains.media.integrations.standard import StandardIntegration
from app.domains.shared import execute_ha_service

log = logging.getLogger(__name__)

class CastIntegration(StandardIntegration):
    """
    Google Cast Integration.
    Adds SmartPowerSync to ensure the physical TV is ON before playing on the Cast device.
    """
    
    @property
    def integration_type(self) -> str:
        return "cast"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Cast device, ensuring TV sibling is powered on.
        """
        # [Music Assistant Wrapper Detection]
        # If targeting a Music Assistant wrapper (e.g., media_player.office_tv_chrome_2),
        # swap to the underlying Cast device (e.g., media_player.office_tv_chrome)
        entity_state = await self._get_entity_state(entity_id, user_creds)
        if entity_state and entity_state.get("attributes", {}).get("mass_player_type"):
            # This is a Music Assistant wrapper
            active_queue = entity_state["attributes"].get("active_queue")
            if active_queue:
                log.info(f"[MASS Unwrap] Detected Music Assistant wrapper '{entity_id}'. Using underlying Cast device: {active_queue}")
                entity_id = active_queue  # Replace with real Cast device
            else:
                log.warning(f"[MASS Unwrap] Entity {entity_id} is a MASS wrapper but has no active_queue. Proceeding anyway.")
        
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
             if video_id:
                 import json
                 
                 # Toggle Strategy: Set True to use yt-dlp (Bypass), False to use App (Login)
                 # True: Direct URL casting (no app launch, but may have extraction issues)
                 # False: YouTube App launch (stable but shows login prompts)
                 BYPASS_YOUTUBE_APP = True
                 
                 # [Strategy 1: Direct Stream Extraction (Bypass App/Login)]
                 if BYPASS_YOUTUBE_APP:
                     # Try to extract direct stream URL to avoid "Who is watching?" prompts
                     direct_stream = await self._extract_direct_stream_url(query)
                     if direct_stream:
                         log.info(f"[CastIntegration] Extracted direct stream. Bypassing YouTube App.")
                         return await execute_ha_service(
                             "media_player", 
                             "play_media", 
                             entity_id, 
                             user_creds, 
                             {
                                 "media_content_id": direct_stream,
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

    def _extract_youtube_id(self, url: str) -> str:
        """Extracts video ID from various YouTube URL formats."""
        # Setup regex for standard and short URLs
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

        return None

    async def _extract_direct_stream_url(self, url: str) -> str:
        """Attempts to extract a direct mp4 stream using yt-dlp."""
        try:
            # Check if yt_dlp is installed
            import yt_dlp
            
            log.info("[CastIntegration] Attempting yt-dlp extraction...")
            
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
            log.warning("[CastIntegration] yt-dlp not installed. Skipping direct stream extraction.")
        except Exception as e:
            log.warning(f"[CastIntegration] yt-dlp extraction failed: {e}")
        
        return None

    async def _ensure_tv_on(self, entity_id: str, user_creds: Dict):
        """
        Finds the physical TV sibling for this Cast device and turns it on if needed.
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
                                    friendly_name = metadata.get("friendly_name", "").lower()
                                    candidate_integration = metadata.get("integration", "")
                                    
                                    # Find device with "tv" in name OR non-MA integration
                                    if (("tv" in friendly_name or "tv" in candidate_id) and 
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
