
import re
import difflib
from collections import defaultdict
from app.settings import log

SUFFIX_CLEANERS = [
    r"\s+remote", r"\s+tv", r"\s+chrome", r"\s+chromecast", r"\s+cast", 
    r"\s+speaker", r"\s+media\s+player", r"\s+assistant", r"\s+device"
]

def normalize_name(name: str) -> str:
    """
    Reduces names to their 'base' form for grouping.
    e.g. "Office TV Chrome" -> "office" (or "office tv" if strict)
    """
    n = name.lower().strip()
    # Remove parens
    n = re.sub(r"\(.*?\)", "", n)
    # Remove common suffixes
    for suffix in SUFFIX_CLEANERS:
        n = re.sub(suffix, "", n)
    return n.strip()

def group_entities(entities: list) -> dict:
    """
    Groups entities into 'Physical Devices'.
    
    Returns:
        Dict[str, Dict]: {
            "group_id": {
                "friendly_name": "Office TV",
                "members": [
                    {"entity_id": "media_player.office_tv", "domain": "media_player", "integration": "androidtv"},
                    {"entity_id": "remote.office_tv", "domain": "remote", "integration": "androidtv"},
                    {"entity_id": "media_player.office_tv_chrome", "domain": "media_player", "integration": "cast"}
                ],
                "capabilities": ["turn_off", "play_media", "remote_control"]
            }
        }
    """
    groups = defaultdict(lambda: {"members": [], "friendly_name": "", "capabilities": set(), "score": 0})
    
    # 1. First Pass: Create Groups Keyed by Normalized Name
    for e in entities:
        # Infer Integration
        integration = "unknown"
        eid = e.get("entity_id", "")
        attrs = e.get("attributes", {})
        
        if "mass" in eid or attrs.get("app_id") == "music_assistant":
            integration = "music_assistant"
        elif "androidtv" in eid or "remote" in eid:
            integration = "androidtv_remote" # Guess
        elif "_chrome" in eid or "cast" in attrs.get("app_name", "").lower():
            integration = "cast"
            
        clean_name = normalize_name(e.get("friendly_name", eid))
        
        # Use first 3 words as key if long? No, use full normalized name.
        if not clean_name: clean_name = "unknown"
        
        group_key = clean_name
        
        # Add to Group
        groups[group_key]["members"].append({
            "entity_id": eid,
            "friendly_name": e.get("friendly_name"),
            "domain": eid.split(".")[0],
            "integration": integration,
            "state": e.get("state"),
            "features": attrs.get("supported_features", 0),
            "attributes": attrs  # Preserve raw attributes for ingestion
        })
        
        # Update Group Meta
        if len(e.get("friendly_name", "")) > len(groups[group_key]["friendly_name"]):
            # Use longest name as label, but stripped? 
            # Actually, use the name that matches the key best?
            # Let's just use the cleanest version of the first member's name
             groups[group_key]["friendly_name"] = clean_name.title()

    # 2. Add Capabilities
    final_groups = {}
    for key, data in groups.items():
        caps = set()
        for m in data["members"]:
            dom = m["domain"]
            if dom == "remote":
                caps.add("remote_control")
                caps.add("turn_off")
                caps.add("turn_on")
            elif dom == "media_player":
                caps.add("play_media")
                feat = m["features"]
                if feat & 256: caps.add("turn_off") # SUPPORT_TURN_OFF
                if feat & 128: caps.add("turn_on")
        
        data["capabilities"] = list(caps)
        final_groups[key] = data

    return final_groups
