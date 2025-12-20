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
                return result
            
            # Retry with type 'search' if specific type failed
            if ctype != "search":
                log.info("[MusicAssistantIntegration] Retrying with media_type='search'...")
                result = await music_assistant_ops.play_media(entity_id, cleaned_query, "search", user_creds)
                if result and result.get("status") == "SUCCESS":
                    return result
            
            return result or {"status": "FAILURE", "message": "Music Assistant delegation failed"}
            
        except Exception as e:
            log.error(f"[MusicAssistantIntegration] Error: {e}")
            return {"status": "FAILURE", "message": str(e)}

    def _clean_query(self, query: str, device_name: str = "") -> str:
        """MA specific cleaner."""
        clean = query.lower()
        
        # Remove device name if known
        if device_name:
            # Simple case-insensitive removal
            d_clean = device_name.lower().strip()
            # Try to remove "on [device_name]" first
            clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?" + re.escape(d_clean) + r"\b", " ", clean)
            # Remove just the device name
            clean = clean.replace(d_clean, " ")
        
        # Remove common MA keywords
        clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean)
        # Remove actions
        clean = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean)
        
        # Remove "on X" pattern at end if it looks like a device (catch-all)
        # Matches: "on office tv", "on the office tv", "in the bedroom"
        # Be careful not to cut off song titles like "Walk on Water"
        # Strategy: matching common room names or "tv"/"speaker"
        clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?(office|living|bedroom|kitchen|garage|patio|tv|speaker|soundbar).*$", "", clean)
        
        # Remove "the" if standalone
        clean = re.sub(r"\bthe\b", "", clean)
        
        # Remove punctuation
        clean = re.sub(r"[^\w\s]", "", clean)
        
        return re.sub(r'\s+', ' ', clean).strip()
