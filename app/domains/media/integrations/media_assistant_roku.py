from typing import Dict, Any, Optional
import logging
import asyncio
import requests
from app.domains.media.integrations.base import MediaIntegration, VideoHelperMixin
from app.domains.shared import execute_ha_service
from app.settings import log

class RokuMediaAssistantIntegration(MediaIntegration, VideoHelperMixin):
    """
    Roku TV Integration using MedievalApple/Media-Assistant (Channel 782875).
    
    This integration replaces the standard Roku integration when enabled.
    It utilizes the Media-Assistant channel for:
    - Rich Music UI (Album Art, Artist, Song Name)
    - Direct Video Playback
    """
    
    service_type = "media_assistant"
    creates_wrapper = False 
    
    # Constants
    MEDIA_ASSISTANT_CHANNEL_ID = "782875" # Store Version
    MEDIA_ASSISTANT_DEV_ID = "dev"
    
    @property
    def integration_type(self) -> str:
        return "roku_media_assistant"
    
    async def get_state(self, entity_id: str, user_creds: Dict) -> Optional[Any]:
        """Get current state of the entity"""
        from app.settings import HA_URL
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
            if resp.status_code == 200:
                from types import SimpleNamespace
                data = resp.json()
                return SimpleNamespace(state=data.get("state"), attributes=data.get("attributes", {}))
            return None
        except Exception as e:
            log.warning(f"[RokuMA] Failed to get state: {e}")
            return None

    async def _get_roku_ip(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """Get Roku IP address using SSDP network discovery (Copied from RokuIntegration for isolation)"""
        from app.settings import HA_URL
        from app.utils.network_discovery import discover_roku_ip
        
        try:
            headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
            resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
            if resp.status_code == 200:
                entity_data = resp.json()
                attributes = entity_data.get("attributes", {})
                
                # Attempt SSDP/Scan discovery
                ip = await discover_roku_ip(attributes)
                if ip:
                    log.info(f"[RokuMA] Discovered IP: {ip}")
                    return ip
                else:
                    log.error(f"[RokuMA] SSDP discovery found no Roku devices for {entity_id}")
                    return None
        except Exception as e:
            log.error(f"[RokuMA] Discovery error: {e}")
            return None

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media using Media-Assistant.
        Supports:
        - Music: t=a, with metadata
        - Video: t=v, with title
        """
        log.info(f"[RokuMA] Playing Media: {query} (Entity: {entity_id}) - Type: {media_type}")
        
        # 2. SmartPowerSync - Ensure TV is ON and Awake
        # Roku 'idle' often means screensaver or standby. We must force wake.
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        # Check current state
        initial_state = await self.get_state(entity_id, user_creds)
        current_state_value = initial_state.state if initial_state else "unknown"
        
        log.info(f"[RokuMA] Smart Power Check: State is '{current_state_value}'")
        
        # If OFF or IDLE, wake it up
        if current_state_value in ["off", "idle", "unavailable", "unknown"]:
            log.info(f"[RokuMA] Device is {current_state_value}, waking up...")
            
            # 1. Turn On Service
            await self.turn_on(entity_id, user_creds)
            await asyncio.sleep(2)
            
            # 2. Send Home Key (Wakes display from screensaver/eco mode)
            log.info(f"[RokuMA] Sending Home button to force display wake...")
            await execute_ha_service("remote", "send_command", remote_entity_id, user_creds, {"command": "Home"}, kwargs.get("redis_client"))
            await asyncio.sleep(2)
            
            # 3. Verify (Optional logging)
            new_state = await self.get_state(entity_id, user_creds)
            log.info(f"[RokuMA] Post-Wake State: {new_state.state if new_state else 'None'}")

        # 3. Prepare Common Params
        params = {}
        
        # 4. Get Roku IP (After wake, to ensure networking is active)
        roku_ip = await self._get_roku_ip(entity_id, user_creds)
        if not roku_ip:
            return {"status": "FAILURE", "message": "Roku IP address not found"}

        # 5. Handle Types
        if media_type == "music":
            # Delegate to Music Assistant's play_media service
            # CRITICAL: Must use the Music Assistant player entity (the one with active_queue)
            from app.logic import music_assistant_ops
            from app.domains.media.devices import find_group_sibling
            
            # Find the Music Assistant player entity for this Roku
            # We look for a sibling in the same group that is a Music Assistant player
            def is_ma_player(m):
                integ = str(m.get("integration", "")).lower()
                attrs = str(m.get("attributes", "")).lower()
                return "music_assistant" in integ or "active_queue" in attrs or "mass_player_type" in attrs

            log.info(f"[RokuMA] Searching for MA player sibling for {entity_id}...")
            ma_player_entity = await find_group_sibling(entity_id, is_ma_player)
            
            if not ma_player_entity:
                # Fallback: check kwargs if find_group_sibling failed (it might if ChromaDB is empty/context missing)
                log.warning(f"[RokuMA] find_group_sibling failed. Checking kwargs.group_members as fallback.")
                group_members = kwargs.get("group_members", [])
                for member in group_members:
                    if member == entity_id: continue
                    try:
                        state = await self.get_state(member, user_creds)
                        if state and state.attributes:
                            if "active_queue" in state.attributes or "mass_player_type" in state.attributes:
                                ma_player_entity = member
                                break
                    except: continue

            if not ma_player_entity:
                log.error(f"[RokuMA] Could not find MA player sibling for {entity_id}")
                return {"status": "FAILURE", "message": "Could not find Music Assistant player entity for this Roku family"}
            
            # Clean Query before sending to MA
            device_name = kwargs.get("device_name", "")
            cleaned_query = self._clean_query(query, device_name)

            # If the query is empty after cleaning, it's likely a generic "Play" command (Resume)
            if not cleaned_query:
                log.info(f"[RokuMA] Cleaned query is empty, redirecting to resume (play) handler")
                return await self.play(entity_id, user_creds, **kwargs)

            # 1. Resolve Metadata for Roku Display
            log.info(f"[RokuMA] Resolving metadata for display: '{cleaned_query}'")
            search_res = await music_assistant_ops.tool_music_search(cleaned_query, user_creds, kwargs.get("redis_client"))
            best_match = None
            if search_res.get("status") == "SUCCESS" and search_res.get("results"):
                # Use the first result (highest score)
                best_match = search_res["results"][0]
                log.info(f"[RokuMA] Found best match: {best_match.get('title')} ({best_match.get('type')})")

            # 2. Populate Params for ECP Launch (Roku UI)
            params = {"t": "a", "autoplay": "true"} # Audio mode
            if best_match:
                params["songName"] = best_match.get("title", "")
                params["artistName"] = best_match.get("artist") or (best_match.get("title", "") if best_match.get("type") == "artist" else "Multiple Artists" if best_match.get("type") == "playlist" else "")
                if best_match.get("image_url"):
                    params["albumArt"] = best_match["image_url"]
                
                # Use the specific URI for the MA service call to be precise
                ma_media_id = best_match.get("media_content_id") or cleaned_query
                ma_media_type = best_match.get("media_content_type") or "music"
            else:
                params["songName"] = cleaned_query
                ma_media_id = cleaned_query
                ma_media_type = "music"

            # 3. Launch the Media Assistant channel (Roku UI)
            # We do this FIRST so the app is open when audio starts
            if roku_ip:
                base_url = f"http://{roku_ip}:8060/launch/{self.MEDIA_ASSISTANT_CHANNEL_ID}"
                try:
                    log.info(f"[RokuMA] Launching Roku UI via ECP: {base_url} | {params}")
                    # Using a short timeout for ECP launch
                    requests.post(base_url, params=params, timeout=5)
                    # Important: Give the Roku app a moment to initialize before sending audio
                    await asyncio.sleep(3)
                except Exception as e:
                    log.warning(f"[RokuMA] ECP Launch failed: {e}")
            else:
                 log.warning(f"[RokuMA] No Roku IP found, skipping ECP launch.")

            # 4. Delegate Audio Playback to Music Assistant
            log.info(f"[RokuMA] Delegating audio to MA service | MA Entity: {ma_player_entity} | Media: {ma_media_id}")
            result = await music_assistant_ops.play_media(ma_player_entity, ma_media_id, ma_media_type, user_creds)
            
            if result and result.get("status") == "SUCCESS":
                log.info(f"[RokuMA] MA service call successful for {ma_player_entity}")
                return result
            else:
                log.warning(f"[RokuMA] MA service call failed: {result}")
                return result or {"status": "FAILURE", "message": "MA service call failed"}

        elif media_type == "video":
            # Video Logic
            
            # Resolve Query if needed (same as StandardIntegration)
            if not query.startswith(("http", "www", "spotify", "app")):
                from app.domains.media.integrations.standard import StandardIntegration
                std_integration = StandardIntegration()
                # Clean query using same logic as standard
                cleaned_query = self._clean_query(query, kwargs.get("device_name", ""))
                
                if not cleaned_query:
                    log.info(f"[RokuMA] Video query empty after cleaning, redirecting to resume")
                    return await self.play(entity_id, user_creds, **kwargs)

                resolved_url = await std_integration._search_video_url(cleaned_query)
                if resolved_url:
                    query = resolved_url
                else:
                     return {"status": "FAILURE", "message": "Could not find a playable video"}

            # Convert to local stream if needed (yt-dlp)
            # We assume query is now a URL.
            # Use VideoHelperMixin to process it
            local_url = await self._download_and_serve_video(query)
            
            if not local_url:
                 return {"status": "FAILURE", "message": "Failed to prepare video stream"}
            
            params["t"] = "v"
            params["u"] = local_url
            params["videoName"] = kwargs.get("media_title", "Video")
            params["videoFormat"] = "hls" if "m3u8" in local_url else "mp4"
            
            log.info(f"[RokuMA] Video Params: {params}")

        else:
            log.warning(f"[RokuMA] Unsupported media type: {media_type}, defaulting to video")
            params["t"] = "v"
            params["u"] = query

        # 4. Launch Media-Assistant
        # Using /launch to force app open and handle args
        # ECP URL: http://IP:8060/launch/782875?args...
        
        base_url = f"http://{roku_ip}:8060/launch/{self.MEDIA_ASSISTANT_CHANNEL_ID}"
        
        try:
            log.info(f"[RokuMA] Sending ECP request to {base_url}")
            resp = requests.post(base_url, params=params, timeout=10)
            
            if resp.status_code == 200:
                # Store last video URL if this was a video playback
                if media_type == "video":
                    redis_client = kwargs.get("redis_client")
                    if redis_client:
                        user = user_creds.get("user", "admin")
                        last_video_key = f"roku_last_video:{entity_id}:{user}"
                        # Store the original query (before local stream conversion)
                        original_query = kwargs.get("original_query", query)
                        redis_client.setex(last_video_key, 3600, original_query)  # 1 hour TTL
                        log.info(f"[RokuMA] Stored last video URL for resume: {original_query}")
                
                return {
                     "status": "SUCCESS",
                     "message": f"Launched Media-Assistant on {entity_id}",
                     "entity_id": entity_id,
                     "service": "roku_ma_launch"
                 }
            else:
                return {
                     "status": "FAILURE", 
                     "message": f"Roku returned {resp.status_code}",
                     "entity_id": entity_id
                 }
                 
        except Exception as e:
            log.error(f"[RokuMA] Request failed: {e}")
            return {"status": "FAILURE", "message": f"Exception: {e}"}

    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Stop media by sending Home command (Exits app).
        """
        # Logic is identical to standard Roku - send Home key
        remote_entity_id = entity_id.replace("media_player.", "remote.")
        
        return await execute_ha_service(
            "remote", "send_command", remote_entity_id, user_creds, {"command": "Home"}, kwargs.get("redis_client")
        )

    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Turn on Roku device"""
        log.info(f"[RokuMA] Turning on {entity_id}")  
        return await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, kwargs.get("redis_client"))

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Turn off Roku - send explicit PowerOff command"""
        from app.domains.media.devices import get_entity_state
        
        log.info(f"[RokuMA] Turning off {entity_id}")
        
        # Get remote entity
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        if not remote_entity_id:
             # Fallback if no remote found (unlikely)
             remote_entity_id = entity_id.replace("media_player.", "remote.")

        # Send PowerOff button
        # This is more effective for Roku TVs than just Home (which only exits apps)
        return await execute_ha_service(
            "remote", 
            "send_command", 
            remote_entity_id, 
            user_creds, 
            {"command": "PowerOff"}, 
            kwargs.get("redis_client")
        )

    async def _get_roku_remote(self, entity_id: str, user_creds: Dict) -> Optional[str]:
        """
        Find the remote entity for this Roku device by checking the same group.
        """
        from app.settings import GlobalResources
        
        remote_entity = None
        
        # Strategy 1: ChromaDB group lookup
        try:
            if GlobalResources.ha_collection:
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    
                    if current_group_id and current_group_id != "unknown":
                        # Find all devices in same group
                        group_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": current_group_id},
                            include=["metadatas"]
                        )
                        
                        if group_docs and group_docs.get("metadatas"):
                            for metadata in group_docs["metadatas"]:
                                candidate_id = metadata.get("entity_id")
                                candidate_domain = candidate_id.split('.')[0] if candidate_id else None
                                
                                # Find remote entity in the same group
                                if (candidate_domain == "remote" and candidate_id != entity_id):
                                    remote_entity = candidate_id
                                    log.info(f"[RokuMA] Found remote entity via group: {remote_entity}")
                                    return remote_entity
        except Exception as e:
            log.warning(f"[RokuMA] ChromaDB lookup failed: {e}")
        
        # Strategy 2: Fallback to simple replacement
        if not remote_entity:
            remote_entity = entity_id.replace("media_player.", "remote.")
            log.info(f"[RokuMA] Using fallback remote entity: {remote_entity}")
        
        return remote_entity

    async def play(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Resume/play for Roku.
        - For music: Use Play button toggle (works with Music Assistant)
        - For video: If device is 'off' (app exited), re-launch last video via Media Assistant
        """
        from app.domains.media.devices import get_entity_state
        
        log.info(f"[RokuMA] Resuming playback on {entity_id}")
        
        # Check current state
        state = await get_entity_state(entity_id, user_creds)
        log.info(f"[RokuMA] Current state: {state}")
        
        # If device is 'off', video app has exited - need to re-launch
        if state == "off":
            log.info(f"[RokuMA] Device is off (video app exited). Checking for last video to re-launch...")
            
            # Check if we have a last video URL stored
            redis_client = kwargs.get("redis_client")
            if redis_client:
                # Try to get last video from Redis
                user = user_creds.get("user", "admin")
                last_video_key = f"roku_last_video:{entity_id}:{user}"
                
                try:
                    last_video_url = redis_client.get(last_video_key)
                    if last_video_url:
                        log.info(f"[RokuMA] Re-launching last video: {last_video_url}")
                        # Re-launch using play_media
                        return await self.play_media(
                            entity_id,
                            last_video_url.decode() if isinstance(last_video_url, bytes) else last_video_url,
                            "video",
                            user_creds,
                            **kwargs
                        )
                except Exception as e:
                    log.warning(f"[RokuMA] Failed to get last video: {e}")
            
            # If no last video, fall back to Play button (may not work)
            log.warning(f"[RokuMA] No last video found, attempting Play button (may fail)")
        
        # For music or if device is not off, use Play button toggle
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play button
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},
            kwargs.get("redis_client")
        )
        
        if result.get("status") == "SUCCESS":
            return {
                "status": "SUCCESS",
                "message": "Resumed playback",
                "entity_id": entity_id,
                "service": "remote.send_command"
            }
        
        return result

    async def pause_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Pause using Play button toggle"""
        log.info(f"[RokuMA] Pausing playback on {entity_id}")
        
        # Get remote entity from same group
        remote_entity_id = await self._get_roku_remote(entity_id, user_creds)
        
        # Send Play button (toggles)
        result = await execute_ha_service(
            "remote",
            "send_command",
            remote_entity_id,
            user_creds,
            {"command": "Play"},
            kwargs.get("redis_client")
        )
        
        if result.get("status") == "SUCCESS":
            return {
                "status": "SUCCESS",
                "message": "Paused playback",
                "entity_id": entity_id,
                "service": "remote.send_command"
            }
        
        return result

    def _clean_query(self, query: str, device_name: str = "") -> str:
        """MA specific cleaner."""
        import re
        # 1. Normalize
        clean = query.lower().replace("'", "").replace("’", "")
        
        # 2. Remove device name if known
        if device_name:
            d_clean = device_name.lower().replace("'", "").replace("’", "").strip()
            # Try to remove "on [device_name]" first
            clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?" + re.escape(d_clean) + r"\b", " ", clean)
            clean = clean.replace(d_clean, " ")
            clean = self._fuzzy_remove_device(clean, d_clean)
        
        # 3. Remove common MA keywords and actions
        clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast|play|please|from|on|open|launch|playback|listen to|now|watch|view|show|me)\b", " ", clean)
        
        # 4. Generic Prepositional Stripping (e.g., "on the TV", "in the office")
        # Cleans phrases at the end of the query if device_name wasn't explicitly found
        clean = re.sub(r"\s+\b(on|in|at|to|from)\b\s+(the\s+)?[\w\s]{2,15}$", "", clean, flags=re.IGNORECASE)

        # 5. Final Cleanup
        clean = re.sub(r"\s+", " ", clean).strip()
        # Remove trailing punctuation
        clean = re.sub(r"[?!.,]$", "", clean)
        
        return clean

    def _fuzzy_remove_device(self, query: str, device_name: str) -> str:
        """Helper for fuzzy device name removal."""
        import difflib
        words = query.split()
        if not words: return query
        
        # Check for fuzzy match of device_name within the query words
        for i in range(len(words)):
            # Check single words or pairs (for "living room")
            for length in [1, 2, 3]:
                if i + length <= len(words):
                    candidate = " ".join(words[i : i + length]).lower()
                    if difflib.SequenceMatcher(None, candidate, device_name.lower()).ratio() > 0.8:
                        # Match! Remove these words
                        new_words = words[:i] + words[i + length :]
                        return " ".join(new_words)
        return query
