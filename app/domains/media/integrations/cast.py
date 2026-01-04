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
            # [Source of Truth] Check passed metadata first (Reliable)
            metadata = kwargs.get("metadata", {})
            
            # Helper to check attributes
            def check_ma_attrs(attrs_dict):
                 return bool(attrs_dict.get("mass_player_type") or attrs_dict.get("music_assistant"))

            is_ma = False
            
            # 1. Check passed metadata
            if metadata:
                 attrs = metadata.get("attributes", {})
                 # If flattened string
                 if isinstance(attrs, str):
                      is_ma = "mass_player_type" in attrs or "music_assistant" in attrs
                 elif isinstance(attrs, dict):
                      is_ma = check_ma_attrs(attrs)
            
            # 2. Fallback to Chroma Lookup (Only if metadata missing) - [Legacy/Backup]


            # 3. [Robustness Fix] If NOT confirmed MA yet, try to find a sibling MA wrapper
            # Use this wrapper as the target for the music command
            if not is_ma:
                ma_wrapper = await self._get_ma_wrapper(entity_id)
                if ma_wrapper:
                    log.info(f"[Cast] Found Music Assistant wrapper ({ma_wrapper}) for {entity_id}. Swapping target for Music request.")
                    entity_id = ma_wrapper
                    is_ma = True

            if is_ma:
                log.info(f"[Cast] Music request on MA wrapper (Source of Truth), delegating to MusicAssistantIntegration")
                from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
                ma_integration = MusicAssistantIntegration()
                return await ma_integration.play_media(entity_id, query, media_type, user_creds, **kwargs)
        
        # [Session Clearing for Video Playback]

        # If device is playing (e.g., Music Assistant session), stop it first
        # to prevent session conflicts when switching to video
        # [Session Clearing for Video Playback]
        # Always stop active session before starting video to ensure clean state.
        # This handles cases where HA state lag reports 'idle' but device is actually playing (e.g. Music Assistant).
        if media_type == "video":
            # Find Music Assistant wrapper in the same group to stop it
            # This is more robust than assuming entity_id + "_2" or similar
            ma_wrapper = await self._get_ma_wrapper(entity_id)
            target_stop = ma_wrapper if ma_wrapper else entity_id
            
            log.info(f"[Session Clear] Force stopping previous session on {target_stop} before video.")
            try:
                await execute_ha_service("media_player", "media_stop", target_stop, user_creds, {}, None)
                await asyncio.sleep(1)  # Wait for stop to take effect
            except Exception as e:
                log.warning(f"[Session Clear] Stop failed (ignoring): {e}")
        
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
             cleaned = self._clean_query(query, media_type, entity_id, kwargs.get("friendly_name"))
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
                                 "media_content_type": "video/mp4" 
                             }, 
                             kwargs.get("redis_client")
                         )

                     return {
                             "status": "FAILURE", 
                             "message": "Failed to download video for casting. YouTube app fallback is disabled."
                         }

        # Proceed with Standard Playback
        # If we updated 'query' to a URL, super() will skip search and just play it.
        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn off Cast device AND physical TV sibling (SmartPowerOff).
        Mirrors the _ensure_tv_on logic for consistency.
        """
        log.info(f"[Cast] Turning off {entity_id} (SmartPowerOff)")
        
        try:
            from app.domains.media.devices import get_entity_state
            
            # 1. Find and turn off physical TV sibling (if exists)
            tv_sibling = await self._get_tv_sibling(entity_id, user_creds)
            
            if tv_sibling:
                try:
                    tv_state = await get_entity_state(tv_sibling, user_creds)
                    if tv_state not in ["off", "standby", "unavailable"]:
                        log.info(f"[SmartPowerOff] TV {tv_sibling} is {tv_state}. Turning OFF.")
                        await execute_ha_service("media_player", "turn_off", tv_sibling, user_creds, {}, None)
                        # No sleep needed for turn off (faster)
                    else:
                        log.info(f"[SmartPowerOff] TV {tv_sibling} is already {tv_state}")
                except Exception as e:
                    log.warning(f"[SmartPowerOff] Failed to turn off {tv_sibling}: {e}")
            else:
                log.warning(f"[SmartPowerOff] No TV sibling found for {entity_id}")
                    
        except Exception as e:
            log.warning(f"[SmartPowerOff] Error: {e}")
        
        # 1.5. Force stop any active playback before turning off
        try:
            cast_state = await get_entity_state(entity_id, user_creds)
            if cast_state in ["playing", "buffering"]:
                log.info(f"[Cast]  Stopping active playback before turn_off (state: {cast_state})")
                await self.stop_media(entity_id, user_creds, **kwargs)
                import asyncio
                await asyncio.sleep(0.3)
        except Exception as e:
            log.warning(f"[Cast] stop_media failed: {e}")
        
        # 2. Turn off Cast device (stops app/session)
        # 2. Turn off Cast device (stops app/session)
        return await super().turn_off(entity_id, user_creds, **kwargs)

    async def _ensure_tv_on(self, entity_id: str, user_creds: Dict):
        """
        Finds the physical TV sibling for this Cast device and turns it on if needed.
        """
        try:
            from app.domains.media.devices import get_entity_state
            
            tv_sibling = await self._get_tv_sibling(entity_id, user_creds)
            
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

    async def _get_tv_sibling(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """
        Find physical TV sibling using group capability lookup.
        """
        from app.domains.media.devices import find_group_sibling
        
        # Define capability matcher for TV
        def is_tv(metadata):
            integ = metadata.get("integration", "")
            # Check device_class from attributes
            attrs_str = metadata.get("attributes", "{}")
            device_class = None
            try:
                import json
                attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                device_class = attrs.get("device_class")
            except: pass
            
            # Match
            return (integ in ["androidtv", "webostv", "samsungtv", "braviatv", "roku", "esphome"] or 
                    device_class == "tv") and integ != "music_assistant"

        return await find_group_sibling(entity_id, is_tv)

    async def _get_ma_wrapper(self, entity_id: str) -> Optional[str]:
        """
        Find Music Assistant wrapper sibling using group capability lookup.
        """
        from app.domains.media.devices import find_group_sibling
        return await find_group_sibling(entity_id, lambda m: m.get("integration") == "music_assistant" or m.get("app_id") == "music_assistant")

    # Removed _find_group_sibling as it is now shared in app.domains.media.devices
