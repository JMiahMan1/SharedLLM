from typing import Dict, Any, List
import logging
import re
from app.domains.media.integrations.base import MediaIntegration
from app.domains.shared import execute_ha_service

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

        service_data = {
            "media_content_id": cleaned_query,
            "media_content_type": media_type
        }
        
        domain = entity_id.split(".")[0]
        return await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

    async def _search_video_url(self, search_query: str) -> str:
        """Search Whoogle for a YouTube URL."""
        log.info(f"[StandardIntegration] Searching Whoogle for '{search_query} youtube'...")
        try:
             from app.logic.web_search import tool_web_search
             search_results = await tool_web_search(f"{search_query} youtube")
             
             # Extract URLs using regex
             url_pattern = r'URL:\s*(https?://[^\s\n]+)'
             urls = re.findall(url_pattern, search_results)
             
             for url in urls:
                 if "youtube.com" in url or "youtu.be" in url:
                     log.info(f"[StandardIntegration] Resolved to {url}")
                     return url
        except Exception as e:
            log.warning(f"[StandardIntegration] Search error: {e}")
        return None

    def _clean_query(self, query: str, media_type: str, entity_id: str, device_name: str = None) -> str:
        """Clean the query string."""
        cleaned = query.lower()
        
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
