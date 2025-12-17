
from app.settings import log

def infer_integration(entity_id: str, attributes: dict, manufacturer: str = None, model: str = None) -> str:
    """
    Infers the integration type based on entity ID, attributes, and registry data.
    """
    eid = entity_id.lower()
    
    
    # 2. MANUFACTURER / MODEL INFERENCE (Generic)
    if manufacturer or model:
        man = str(manufacturer).lower() if manufacturer else ""
        mod = str(model).lower() if model else ""
        
        # Generic Android TV / Shield detection
        if "android" in man or "android" in mod:
             return "androidtv"
        if "nvidia" in man and "shield" in mod:
             return "androidtv"
        
        # Note: Previous hardcoded 'askey' rule removed.
        # Logic now relies on device_class or generic name matching below.
            
        # Roku
        if "roku" in man:
            return "roku"

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
        fname = attributes.get("friendly_name", "").lower()
        
        # Robust Cast/Android Detection
        # Android TV often uses 'com.google.android.youtube.tv' or similar package names
        if app_id and ("android" in str(app_id).lower() or "." in str(app_id)):
            return "androidtv"
            
        # Standard Cast IDs (Netflix, YouTube, Default Media Receiver) or Keywords
        if app_id or "chromecast" in fname or "google cast" in fname:
            return "cast"
            
        if "android" in eid or "shield" in eid or "fire" in eid:
             return "androidtv"
        
        # Explicit Cast Check in Entity ID
        if "cast" in eid or "chrome" in eid:
             return "cast"

        # Standard Google Home Device Types (from documentation)
        if device_class in ["tv", "settop", "streaming_box", "streaming_soundbar"]:
            return "tv" # Generic TV control
            
        if device_class == "speaker":
            return "speaker"

    # 4. Fallback based on name
    if "spotify" in eid: return "spotify"
    if "plex" in eid: return "plex"
    if "roku" in eid: return "roku"

    return "unknown"
