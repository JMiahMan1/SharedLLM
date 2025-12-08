
from settings import log

def infer_integration(entity_id: str, attributes: dict) -> str:
    """
    Infers the integration type based on entity ID and attributes.
    """
    eid = entity_id.lower()
    
    # 1. Explicit Integration Check (if provided in registry data)
    if "integration" in attributes:
        return attributes["integration"]

    # 2. Remote Domain
    if eid.startswith("remote."):
        if "android" in eid: return "androidtv_remote"
        if "apple" in eid: return "apple_tv"
        return "remote"

    # 3. Media Player Logic
    if eid.startswith("media_player."):
        # Check for Music Assistant
        if "mass" in eid or attributes.get("integration") == "music_assistant":
            return "music_assistant"
        
        # Check for Cast / Android TV via attributes
        app_id = attributes.get("app_id")
        device_class = attributes.get("device_class")
        
        if "_chrome" in eid or "_cast" in eid:
            return "cast"
            
        if "android" in eid or app_id == "com.google.android.youtube.tv":
            # Likely Android TV
            return "androidtv"
            
        if device_class == "tv":
            return "tv"
            
        if device_class == "speaker":
            return "speaker"

    # 4. Fallback based on name
    if "spotify" in eid: return "spotify"
    if "plex" in eid: return "plex"
    if "roku" in eid: return "roku"

    return "unknown"
