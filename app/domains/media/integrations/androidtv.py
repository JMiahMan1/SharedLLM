from typing import Dict, Any
import logging
import asyncio
import requests
from app.domains.media.integrations.standard import StandardIntegration
from app.domains.media.integrations.base import VideoHelperMixin
from app.domains.shared import execute_ha_service
from app.settings import run_blocking, HA_URL

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

    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn on Android TV with explicit remote commands to handle 'idle' (screen off) states.
        """
        log.info(f"[AndroidTV] Turning on {entity_id}")
        redis_client = kwargs.get("redis_client")
        
        # 1. Standard Turn On
        # Many Android TVs respond to this, but some ignore it if "idle".
        await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, redis_client)
        
        # 2. Remote Wake-up (Kitchen Sink)
        # Try to find a sibling remote and send 'POWER' or 'WAKEUP'
        # We need a quick way to find the remote. 
        # StandardIntegration doesn't have _get_roku_remote, we should implement a generic _get_remote or just look for it.
        # Most often it's remote.<name> matching media_player.<name>
        
        remote_entity_id = None
        # Naive guess first: replace domain
        candidate = entity_id.replace("media_player.", "remote.")
        # Check against HA state to see if it exists? We don't have easy synchronous check here without overhead.
        # But we can just try sending blindly if we trust the naming, or use the exact sibling logic.
        # Let's try the safe 'send if exists' approach if we can confirm it. 
        # Actually execute_ha_service fails gracefully usually? No, it might log error.
        
        # Better: Quick metadata check if we have it?
        # Let's implement a quick helper here or just duplicate the simple lookup.
        try:
            # Try to resolve remote via simple string substitution which works for 90% of HA setups
            # If standard naming conventions are followed.
            remote_entity_id = entity_id.replace("media_player", "remote")
            
            # Send explicit Power toggle/on
            # Android keys: POWER, WAKEUP. 'POWER' is safer single-button mapping usually.
            log.info(f"[AndroidTV] Sending explicit 'POWER' to {remote_entity_id}")
            await execute_ha_service(
                "remote", "send_command", remote_entity_id, user_creds, 
                {"command": "POWER"}, redis_client
            )
        except Exception:
            pass
            
        return {"status": "SUCCESS"}

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Explicit turn off for Android TV.
        """
        log.info(f"[AndroidTV] Turning off {entity_id}")
        
        # 1. Standard Turn Off
        await execute_ha_service("media_player", "turn_off", entity_id, user_creds, {}, kwargs.get("redis_client"))
        
        # 2. Remote Power (Backup)
        try:
            remote_entity_id = entity_id.replace("media_player", "remote")
            await execute_ha_service(
                "remote", "send_command", remote_entity_id, user_creds, 
                {"command": "POWER"}, kwargs.get("redis_client")
            )
        except Exception: pass
        
        return {"status": "SUCCESS"}

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
                             # [Strengthened Filter]
                             integ = meta.get("integration", "").lower()
                             eid = meta.get("entity_id", "").lower()
                             
                             # 1. Must be 'cast' integration
                             if "cast" not in integ:
                                 return False
                             
                             # 2. explicit MA exclusion by Integration Name
                             if "music_assistant" in integ or "mass" in integ:
                                 return False

                             # 3. Check attributes for known MA signatures
                             attrs = meta.get("attributes", {})
                             attrs_str = str(attrs).lower()
                             if "mass_player_type" in attrs_str or "music_assistant" in attrs_str:
                                 return False
                             
                             # 4. Check Entity ID naming conventions for MA
                             # MA often appends _2, _3 or uses 'mass_' prefix (though usually hidden)
                             # If we have a choice, we prefer the one strictly named 'cast' or '_chrome'
                             # But for exclusion:
                             if "mass_" in eid:
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
                     
                     # Ensure volume is audible on the target
                     await self._ensure_volume_safe(entity_id, user_creds, redis_client)
                     if entity_id != target_entity:
                         await self._ensure_volume_safe(target_entity, user_creds, redis_client)
                     # Explicitly stop the target first to clear any MA session overlay
                     try:
                         # 1. Trigger HOME on Android TV (Source) to ensure visual takeover
                         if entity_id != target_entity:
                              log.info(f"[AndroidTV] Calling nav_home on source {entity_id}")
                              try:
                                  await self.nav_home(entity_id, user_creds, **kwargs)
                              except Exception: pass

                         # 2. Stop the Cast entity
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

    async def _ensure_volume_safe(self, entity_id: str, user_creds: Dict, redis_client=None):
        """Ensure the device is not muted and volume is at least 10% (0.1)."""
        if not HA_URL: return
        
        try:
            # 1. Fetch current state safely (Live API)
            url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
            headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
            
            def _fetch():
                return requests.get(url, headers=headers, timeout=2.0)
            
            r = await run_blocking(_fetch)
            if r.status_code != 200:
                return

            state_data = r.json()
            attrs = state_data.get("attributes", {})
            
            # 2. Check & Fix Mute / Volume
            is_muted = attrs.get("is_volume_muted")
            vol = attrs.get("volume_level")

            if is_muted:
                log.info(f"[Volume Safeguard] Device {entity_id} is MUTED. Initiating Unmute Sequence...")
                
                # 1. Unmute
                res_unmute = await execute_ha_service("media_player", "volume_mute", entity_id, user_creds, {"is_volume_muted": False}, redis_client)
                log.info(f"[Volume Safeguard] Unmute Result: {res_unmute}")
                await asyncio.sleep(1) # Wait for processing
                
                # 2. Set Volume to 20%
                log.info(f"[Volume Safeguard] Setting volume to 20%...")
                res_vol = await execute_ha_service("media_player", "volume_set", entity_id, user_creds, {"volume_level": 0.2}, redis_client)
                log.info(f"[Volume Safeguard] Volume Set Result: {res_vol}")
                await asyncio.sleep(1) # Wait for propagation
                
            elif vol is not None and isinstance(vol, (int, float)):
                 # Not muted, check for blasting levels
                 if vol >= 0.9:
                      log.info(f"[Volume Safeguard] Volume {vol} is too high (>90%). Reducing to 60% on {entity_id}")
                      await execute_ha_service("media_player", "volume_set", entity_id, user_creds, {"volume_level": 0.6}, redis_client)
                      await asyncio.sleep(1)
                 elif vol < 0.1:
                      log.info(f"[Volume Safeguard] Volume {vol} is too low (<10%). Boosting to 20% on {entity_id}")
                      await execute_ha_service("media_player", "volume_set", entity_id, user_creds, {"volume_level": 0.2}, redis_client)
                      await asyncio.sleep(1)
        except Exception as e:
            log.warning(f"[Volume Safeguard] Failed to check/set volume for {entity_id}: {e}")
    async def media_play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Resume playback, ensuring explicit volume safety (e.g. unmuted, >=20%)."""
        log.info(f"[AndroidTV] Play/Resume requested for {entity_id}. Enforcing volume safety.")
        # User requested: "Anytime we go to play audio... move volume up to .20 if muted"
        # We assume entity_id here is the primary Android TV entity or the delegated Cast sibling.
        # Since 'media_play' often targets the entity tracked by HA, verify if it's the cast sibling first?
        # Actually, standard media_play just passes entity_id.
        # We'll run safety check on the target entity.
        
        await self._ensure_volume_safe(entity_id, user_creds, kwargs.get("redis_client"))
        
        # Proceed with standard resume
        return await super().media_play(entity_id, user_creds, **kwargs)
