"""Music Assistant client wrapper for HA service calls."""
import logging
from typing import List, Dict, Any
from ha_client import call_service, find_mass_config_entry

log = logging.getLogger(__name__)

_mass_config_entry_id = ""

async def get_playlists(ha_url: str, ha_token: str) -> List[Dict[str, Any]]:
    """Get Music Assistant playlists."""
    global _mass_config_entry_id
    if not _mass_config_entry_id:
        _mass_config_entry_id = await find_mass_config_entry(ha_url, ha_token)
    
    try:
        result = await call_service(
            ha_url, ha_token, "music_assistant", "playlists", "",
            {"config_entry_id": _mass_config_entry_id}
        )
        if result.get("status") == "SUCCESS" and result.get("data"):
            return [
                {
                    "name": pl.get("name", ""),
                    "items": pl.get("num_tracks", pl.get("items", 0)),
                    "uri": pl.get("uri", ""),
                }
                for pl in result["data"]
            ]
    except Exception as e:
        log.error(f"[mass] Failed to get playlists: {e}")
    return []


async def get_recent(ha_url: str, ha_token: str) -> List[Dict[str, Any]]:
    """Get Music Assistant recently played items."""
    global _mass_config_entry_id
    if not _mass_config_entry_id:
        _mass_config_entry_id = await find_mass_config_entry(ha_url, ha_token)
    
    try:
        result = await call_service(
            ha_url, ha_token, "music_assistant", "recently_played", "",
            {"config_entry_id": _mass_config_entry_id, "limit": 20}
        )
        if result.get("status") == "SUCCESS" and result.get("data"):
            return [
                {
                    "name": item.get("name", ""),
                    "artist": item.get("artist", ""),
                    "uri": item.get("uri", ""),
                    "last_played": item.get("last_played", item.get("timestamp", "")),
                }
                for item in result["data"]
            ]
    except Exception as e:
        log.error(f"[mass] Failed to get recent: {e}")
    return []
