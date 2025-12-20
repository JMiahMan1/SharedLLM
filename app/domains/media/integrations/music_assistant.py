from typing import Dict, Any
import logging
import re
from app.domains.media.integrations.base import MediaIntegration
from app.logic import music_assistant_ops

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
        Delegate to Music Assistant.
        """
        # Determine content type (Default to music for MA)
        ctype = "video" if media_type == "video" else "music"
        
        # Clean Query (Reuse the logic or specialized MA cleaning)
        cleaned_query = self._clean_query(query)
        log.info(f"[MusicAssistantIntegration] Play on {entity_id} | Query: '{cleaned_query}' | Type: {ctype}")

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

    def _clean_query(self, query: str) -> str:
        """MA specific cleaner."""
        clean = query.lower()
        # Remove common MA keywords
        clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean)
        # Remove actions
        clean = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean)
        # Remove device names (simple approach)
        # Remove "on X" pattern at end, handling "the"
        # Matches: "on office tv", "on the office tv", "in the bedroom"
        clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?.*$", "", clean)
        
        # Remove "the" if standalone
        clean = re.sub(r"\bthe\b", "", clean)
        
        # Remove punctuation
        clean = re.sub(r"[^\w\s]", "", clean)
        
        return re.sub(r'\s+', ' ', clean).strip()
