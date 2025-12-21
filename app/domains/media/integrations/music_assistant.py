from typing import Dict, Any
import logging
import re
from app.domains.media.integrations.base import MediaIntegration
from app.settings import log
# Lazy import to avoid circular dependency
# from app.logic import music_assistant_ops

log = logging.getLogger(__name__)

class MusicAssistantIntegration(MediaIntegration):
    """
    Music Assistant Integration.
    Handles delegation to music_assistant_ops and specific cleaners.
    """
    
    # Service Registry Metadata
    service_type = "music"
    creates_wrapper = True
    wrapper_detection = {
        "attribute": "mass_player_type",
        "underlying_device_attribute": "active_queue"
    }
    unwrap_for_request_types = ["video", "transport"]  # Keep wrapper for music requests
    
    @property
    def integration_type(self) -> str:
        return "music_assistant"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute play_media logic for Music Assistant.
        """
        from app.logic import music_assistant_ops 
        
        # 1. Clean Query
        # If media_type is music, we want to strip punctuation and common words
        # Determine content type (Default to music for MA)
        ctype = "video" if media_type == "video" else "music"
        
        # Extract device name to help cleaning
        device_name = kwargs.get("device_name") or ""
        if not device_name and "metadata" in kwargs:
             device_name = kwargs["metadata"].get("friendly_name") or ""
        
        # Clean Query (Reuse the logic or specialized MA cleaning)
        cleaned_query = self._clean_query(query, device_name)
        log.info(f"[MusicAssistantIntegration] Play on {entity_id} | Query: '{cleaned_query}' | Type: {ctype} | Device Removed: '{device_name}'")

        # Attempt MA Delegation
        try:
            result = await music_assistant_ops.play_media(entity_id, cleaned_query, ctype, user_creds)
            
            if result and result.get("status") == "SUCCESS":
                # [Context Update] Critical: Update Redis so subsequent commands (Skip/Pause) target this entity
                redis_client = kwargs.get("redis_client")
                if redis_client:
                    from app.domains.media.devices import _set_last_entity
                    user = user_creds.get("user", "admin")
                    _set_last_entity(redis_client, user, entity_id)
                    log.info(f"[MusicAssistantIntegration] Context updated: {user} -> {entity_id}")
                
                return result
            
            # Retry with type 'search' if specific type failed
            if ctype != "search":
                log.info("[MusicAssistantIntegration] Retrying with media_type='search'...")
                result = await music_assistant_ops.play_media(entity_id, cleaned_query, "search", user_creds)
                if result and result.get("status") == "SUCCESS":
                    return result
            
            # Check for failure with 500 error (implies Clean Search but No Results in MA)
            # Music Assistant throws 500 when specific search returns empty
            if result and result.get("status") == "FAILURE" and "500" in str(result.get("message", "")):
                log.warning(f"[MusicAssistantIntegration] MA returned 500, converting to Not Found. Query: '{cleaned_query}'")
                return {
                    "status": "FAILURE", 
                    "message": f"I couldn't find any music matching '{cleaned_query}' in your library."
                }
                
            return result or {"status": "FAILURE", "message": "Music Assistant delegation failed"}
            
        except Exception as e:
            log.error(f"[MusicAssistantIntegration] Error: {e}")
            return {"status": "FAILURE", "message": f"I encountered an error trying to play '{cleaned_query}' ({str(e)})"}

    def _clean_query(self, query: str, device_name: str = "") -> str:
        """MA specific cleaner."""
        # 1. Normalize: Lowercase and strip apostrophes early to match entity names (e.g. Gracie's -> gracies)
        clean = query.lower().replace("'", "").replace("’", "")
        
        # 2. Remove device name if known
        if device_name:
            # Normalize device name too to match the query
            d_clean = device_name.lower().replace("'", "").replace("’", "").strip()
            # Try to remove "on [device_name]" first
            clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?" + re.escape(d_clean) + r"\b", " ", clean)
            # Remove just the device name
            clean = clean.replace(d_clean, " ")
            
            # Fuzzy Removal for near-misses (e.g. Grace's vs Gracies)
            # We always attempt fuzzy removal if exact removal might have missed
            # The fuzzy cleaner uses a high threshold so it's safe to call
            clean = self._fuzzy_remove_device(clean, d_clean)
        
        # 3. Remove common MA keywords
        clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean)
        # 4. Remove actions
        clean = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean)
        
        # 5. Remove "the" if standalone
        clean = re.sub(r"\bthe\b", "", clean)
        
        # 6. Remove remaining punctuation
        clean = re.sub(r"[^\w\s]", "", clean)
        
        return re.sub(r'\s+', ' ', clean).strip()

    def _fuzzy_remove_device(self, query: str, device_name: str) -> str:
        """
        Attempts to remove the device name from the query using fuzzy matching.
        Useful when 'Grace\'s TV' is spoken but 'Gracies TV' is the entity.
        Slows down processing slightly so only used if strict match fails.
        """
        import difflib
        
        # 1. Normalize both
        q_tokens = query.split()
        d_tokens = device_name.split()
        
        if not d_tokens or not q_tokens:
            return query
            
        # 2. Check overlap at the end of the query
        # We assume the device name is mentioned at the end (e.g. "Play X on [Device]")
        # We try to match the last N tokens where N is len(device_tokens) +/- 1
        
        n = len(d_tokens)
        
        # Try exact length match at end
        if len(q_tokens) >= n:
            suffix = " ".join(q_tokens[-n:])
            ratio = difflib.SequenceMatcher(None, suffix, device_name).ratio()
            if ratio > 0.8: # High confidence
                return " ".join(q_tokens[:-n])
                
        # Try N+1 (e.g. "The Living Room TV")
        if len(q_tokens) >= n + 1:
            suffix = " ".join(q_tokens[-(n+1):])
            ratio = difflib.SequenceMatcher(None, suffix, device_name).ratio()
            if ratio > 0.8:
                 return " ".join(q_tokens[:-(n+1)])
                 
        return query
