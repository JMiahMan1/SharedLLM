from typing import Dict, Any, List
import logging
import re
from app.domains.media.integrations.base import MediaIntegration
from app.domains.media.integrations.base import MediaIntegration
from app.domains.shared import execute_ha_service
import asyncio

log = logging.getLogger(__name__)

class StandardIntegration(MediaIntegration):
    """
    Standard Home Assistant Media Player Integration.
    Handles generic media_player.play_media calls and video search fallback.
    """
    
    @property
    def integration_type(self) -> str:
        return "standard"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Standard playback with Whoogle Search fallback for videos.
        """
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
                 if isinstance(attrs, str):
                      is_ma = "mass_player_type" in attrs or "music_assistant" in attrs
                 elif isinstance(attrs, dict):
                      is_ma = check_ma_attrs(attrs)
            
            # 2. Fallback to Chroma Lookup (Legacy)
            if not is_ma and not metadata:
                try:
                    from app.settings import GlobalResources
                    docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    if docs and docs.get("metadatas"):
                        import json
                        attrs_str = docs["metadatas"][0].get("attributes", "{}")
                        attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                        is_ma = check_ma_attrs(attrs)
                except Exception:
                    pass

            if is_ma:
                log.info(f"[Standard] Music request on MA wrapper (Source of Truth), delegating to MusicAssistantIntegration")
                from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
                ma_integration = MusicAssistantIntegration()
                return await ma_integration.play_media(entity_id, query, media_type, user_creds, **kwargs)

        redis_client = kwargs.get("redis_client")
        
        # CLEAN QUERY
        cleaned_query = self._clean_query(query, media_type, entity_id, kwargs.get("device_name"))
        log.info(f"[StandardIntegration] Play on {entity_id} | Type: {media_type} | Query: '{cleaned_query}'")

        # Video Search Logic
        if media_type == "video" and not query.startswith(("http", "www", "spotify", "app")):
            found_url = await self._search_video_url(cleaned_query)
            if found_url:
                cleaned_query = found_url
            else:
                 return {
                     "status": "FAILURE", 
                     "message": "Video playback requires a direct URL or specific app. Please provide a link.", 
                     "entity_id": entity_id, 
                     "service": "play_media"
                 }

        if media_type == "video" and ("youtube.com" in cleaned_query or "youtu.be" in cleaned_query):
            media_type = "youtube"
            log.info(f"[StandardIntegration] Detected YouTube URL. Switched type to 'youtube' for Cast compatibility.")

        service_data = {
            "media_content_id": cleaned_query,
            "media_content_type": media_type
        }
        
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

    async def _get_remote_sibling(self, entity_id: str) -> str:
        """Find a remote entity in the same group."""
        try:
            from app.settings import GlobalResources
            if GlobalResources.ha_collection:
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if docs and docs.get("metadatas"):
                    meta = docs["metadatas"][0]
                    group_id = meta.get("group_id")
                    
                    if group_id and group_id != "unknown":
                        # Search for remote in the same group
                        group_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": group_id},
                            include=["metadatas"]
                        )
                        if group_docs and group_docs.get("metadatas"):
                            for d_meta in group_docs["metadatas"]:
                                cand_id = d_meta.get("entity_id")
                                if cand_id.startswith("remote."):
                                    log.info(f"[StandardIntegration] Found remote sibling: {cand_id}")
                                    return cand_id
        except Exception as e:
            log.warning(f"[StandardIntegration] Remote sibling lookup failed: {e}")
        return None

    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn on the media player device.
        Prioritizes Remote entity for reliable power control (Android TV Remote).
        """
        log.info(f"[StandardIntegration] Turning on {entity_id}")
        
        # 0. Check for Remote Sibling (Preferred for Power)
        remote_sibling = await self._get_remote_sibling(entity_id)
        if remote_sibling:
             log.info(f"[StandardIntegration] Using remote sibling {remote_sibling} for turn_on")
             # Try turn_on first (androidtv_remote supports this)
             res = await execute_ha_service("remote", "turn_on", remote_sibling, user_creds, {}, kwargs.get("redis_client"))
             if res.get("status") == "SUCCESS":
                 return res
             # Fallback to Power Toggle command if turn_on fails or isn't supported
             log.info(f"[StandardIntegration] Remote turn_on failed or unsupported, trying 'Power' command...")
             return await execute_ha_service("remote", "send_command", remote_sibling, user_creds, {"command": "POWER"}, kwargs.get("redis_client"))

        # 1. Standard Turn On
        domain = entity_id.split(".")[0]
        result = await execute_ha_service(domain, "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))
        
        # 2. Smart Power: Check for TV Sibling (as before)
        try:
            from app.settings import GlobalResources
            if GlobalResources.ha_collection:
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if docs and docs.get("metadatas"):
                    meta = docs["metadatas"][0]
                    integ = meta.get("integration", "").lower()
                    attrs_str = meta.get("attributes", "{}")
                    is_tv = "tv" in integ or "tv" in attrs_str.lower()
                    
                    if not is_tv:
                        group_id = meta.get("group_id")
                        if group_id and group_id != "unknown":
                            group_docs = GlobalResources.ha_collection._collection.get(
                                where={"group_id": group_id},
                                include=["metadatas"]
                            )
                            if group_docs and group_docs.get("metadatas"):
                                for d_meta in group_docs["metadatas"]:
                                    cand_id = d_meta.get("entity_id")
                                    cand_integ = d_meta.get("integration", "").lower()
                                    if cand_id != entity_id and ("tv" in cand_integ or "roku" in cand_integ):
                                        log.info(f"[StandardIntegration] Smart Power: Found TV sibling {cand_id}. Turning ON.")
                                        await execute_ha_service(cand_id.split('.')[0], "turn_on", cand_id, user_creds, {}, kwargs.get("redis_client"))
        except Exception as e:
            log.warning(f"[StandardIntegration] Smart Power check failed: {e}")
            
        return result

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Turn off the media player device.
        Prioritizes Remote entity for reliable power control.
        """
        log.info(f"[StandardIntegration] Turning off {entity_id}")
        
        # 0. Check for Remote Sibling
        remote_sibling = await self._get_remote_sibling(entity_id)
        if remote_sibling:
             log.info(f"[StandardIntegration] Using remote sibling {remote_sibling} for turn_off")
             # Force Stop first (Helps Cast devices release locks)
             await execute_ha_service(entity_id.split('.')[0], "media_stop", entity_id, user_creds, {}, kwargs.get("redis_client"))
             await asyncio.sleep(1)
             
             # Send Turn Off
             return await execute_ha_service("remote", "turn_off", remote_sibling, user_creds, {}, kwargs.get("redis_client"))

        # For Android TV (Legacy/Direct Check)
        is_android_tv = False
        try:
            from app.settings import GlobalResources
            if GlobalResources.ha_collection:
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if docs and docs.get("metadatas"):
                    meta = docs["metadatas"][0]
                    integration = meta.get("integration", "").lower()
                    platform = meta.get("platform", "").lower()
                    if "androidtv" in integration or "androidtv" in platform or "android" in platform:
                        is_android_tv = True
        except Exception: 
            pass
        
        if is_android_tv:
            return await execute_ha_service("androidtv", "turn_off", entity_id, user_creds, {}, kwargs.get("redis_client"))
        
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "turn_off", entity_id, user_creds, {}, kwargs.get("redis_client"))

    async def _search_video_url(self, search_query: str) -> str:
        """Search Whoogle for a YouTube URL."""
        log.info(f"[StandardIntegration] Searching Whoogle for '{search_query} youtube'...")
        try:
             from app.logic.web_search import tool_web_search
             search_results = await tool_web_search(f"{search_query} youtube")
             
             # Extract URLs using regex from Markdown output
             url_pattern = r'URL:\s*(https?://[^\s\n]+)'
             urls = re.findall(url_pattern, search_results)
             
             best_match = None
             
             for url in urls:
                 # Filter OUT Channel/User pages - they are not playable
                 if any(x in url for x in ["/channel/", "/user/", "/@"]):
                     log.info(f"[StandardIntegration] Skipping Channel URL: {url}")
                     continue

                 # Prioritize Valid Video URLs
                 if "youtube.com/watch?v=" in url or "youtu.be/" in url:
                     log.info(f"[StandardIntegration] Found precise video match: {url}")
                     return url
                     
                 # Allow playlists
                 if "youtube.com/playlist?list=" in url:
                     log.info(f"[StandardIntegration] Found playlist match: {url}")
                     best_match = url # Keep looking for single video, but use as backup
                     continue

                 # Store other YouTube links as fallback (e.g., /embed/)
                 if "youtube.com" in url or "youtu.be" in url:
                     if not best_match: best_match = url

             if best_match:
                 log.info(f"[StandardIntegration] Resolved to (fallback): {best_match}")
                 return best_match
                 
        except Exception as e:
            log.warning(f"[StandardIntegration] Search error: {e}")
        return None

    def _clean_query(self, query: str, media_type: str, entity_id: str, device_name: str = None) -> str:
        """Clean the query string."""
        cleaned = query.lower()
        # Remove possessives (e.g. "gracie's" -> "gracie")
        cleaned = cleaned.replace("'s", "")
        
        # Remove device names
        targets_to_remove = ["office tv", "master bedroom tv", "gracie tv", "tv", "speaker"]
        if device_name: targets_to_remove.append(device_name.lower())
        
        # Extract name from entity_id if possible
        if entity_id:
             ename = entity_id.split(".")[-1].replace("_", " ").lower()
             targets_to_remove.append(ename)
             
        for name in targets_to_remove:
            if name and name in cleaned:
                cleaned = re.sub(f"\\b(on|in|at|to)?\\s*(the)?\\s*{re.escape(name)}\\b", " ", cleaned)

        # Remove action words
        cleaned = re.sub(r"\b(play|please|from|on|listen to|watch|view)\b", "", cleaned).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned
        
    async def next_track(self, entity_id: str, user_creds: Dict) -> Dict[str, Any]:
        """Skip to next track."""
        domain = entity_id.split(".")[0]
        result = await execute_ha_service(domain, "media_next_track", entity_id, user_creds)
        
        if result.get("status") == "FAILURE":
            # Attempt fallback to Music Assistant wrapper
            return await self._try_ma_fallback(entity_id, "media_next_track", user_creds) or result
            
        return result

    async def previous_track(self, entity_id: str, user_creds: Dict) -> Dict[str, Any]:
        """Skip to previous track."""
        domain = entity_id.split(".")[0]
        result = await execute_ha_service(domain, "media_previous_track", entity_id, user_creds)
        
        if result.get("status") == "FAILURE":
             # Attempt fallback to Music Assistant wrapper
            return await self._try_ma_fallback(entity_id, "media_previous_track", user_creds) or result
            
        return result

    async def _try_ma_fallback(self, original_entity_id: str, service: str, user_creds: Dict) -> Any:
        try:
             # Try common MASS naming pattern: media_player.mass_[name]
             name_part = original_entity_id.split(".")[-1]
             candidates = [f"media_player.mass_{name_part}"]
             
             # Also try searching DB for integration=music_assistant
             from app.settings import GlobalResources
             if GlobalResources.ha_collection:
                 docs = GlobalResources.ha_collection.similarity_search(original_entity_id, k=5)
                 for d in docs:
                     if d.metadata.get("integration") == "music_assistant":
                         candidates.append(d.metadata.get("entity_id"))
             
             log.info(f"[Standard] Transport failed on {original_entity_id}. Trying MA candidates: {candidates}")
             
             for cand in candidates:
                 res = await execute_ha_service("media_player", service, cand, user_creds)
                 if res.get("status") == "SUCCESS":
                     log.info(f"[Standard] MA Fallback SUCCESS on {cand}. Updating context.")
                     # Fix the context for next time
                     # (We can't easily access redis_client here without changing signature, 
                     # but getting it right once is good enough for now, user will likely 'play' again soon)
                     return res
                     
        except Exception as e:
            log.warning(f"[Standard] MA Fallback error: {e}")
            
        return None
