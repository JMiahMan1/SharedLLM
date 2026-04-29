
import re
import difflib
from collections import defaultdict
from app.settings import log

# Suffixes that indicate a 'wrapper' or 'sub-component' of a physical device.
# We clean these to find the 'base' device name.
SUFFIX_CLEANERS = [
    r"\s+remote", r"\s+chrome", r"\s+chromecast", r"\s+cast", 
    r"\s+media\s+player", r"\s+assistant", r"\s+device"
]
# Note: "TV" and "Speaker" are REMOVED from generic cleaners to prevent
# "Office TV" and "Office Speaker" from merging into "Office".

def normalize_name(name: str) -> str:
    """
    Reduces names to their 'base' form for grouping.
    e.g. "Office TV Chrome" -> "office tv"
    """
    n = name.lower().strip()
    # Remove parens
    n = re.sub(r"\(.*?\)", "", n)
    # Remove common 'wrapper' suffixes
    for suffix in SUFFIX_CLEANERS:
        n = re.sub(suffix, "", n)
    return n.strip()

def group_entities(entities: list, device_map: dict = None) -> dict:
    """
    Groups entities into 'Physical Devices'.
    
    Returns:
        Dict[str, Dict]: {
            "group_id": { "friendly_name": "Office TV", "members": [...], "capabilities": [...] }
        }
    """
    groups = defaultdict(lambda: {"members": [], "friendly_name": "", "capabilities": set(), "score": 0})
    if device_map is None: device_map = {}
    
    # 1. First Pass: Create Groups
    for e in entities:
        eid = e.get("entity_id", "")
        attrs = e.get("attributes", {})
        
        # Get Registry Data
        reg = device_map.get(eid, {})
        did = reg.get("device_id")
        man = reg.get("manufacturer")
        mod = reg.get("model")
        
        
        # Determine Group Key (Unification Strategy)
        group_key = None
        
        friendly_name = attrs.get("friendly_name", eid)
        clean_name = normalize_name(friendly_name)
        if not clean_name: clean_name = "unknown"
        
        # Strategy 1: Hardware Identity (Manufacturer + Model + Optional Name)
        # This is the STRONGEST signal. e.g. "Askey + STI6140D360 + Office"
        # If Man/Mod matches, it's likely the same physical board/device.
        if man and mod:
             # Include clean_name to distinguish multiple TVs of same model (e.g. "Living Room TV" vs "Bed TV")
             group_key = f"hw:{man}:{mod}:{clean_name}".lower()
             
        # Strategy 1.5: Sendspin Protocol Clustering (HA 2026.4)
        elif attrs.get("protocol") == "sendspin":
            if attrs.get("device_class") == "visualizer":
                # Do not group visualizers with audio sinks
                group_key = f"visualizer:{clean_name}"
            else:
                group_key = f"sendspin:{clean_name}"
        
        # Strategy 2: Native HA Device ID (Strongest Native link)
        elif did:
            group_key = f"did:{did}"
            
        # Strategy 3: Name-Based Sibling Relationship
        # Only as a last resort, and we check if it looks like a wrapper (contains "Chrome", "Remote", etc.)
        # If it's JUST 'Office', we DON'T merge.
        else:
            is_wrapper = any(s.strip() in friendly_name.lower() for s in SUFFIX_CLEANERS)
            if is_wrapper:
                 group_key = f"name:{clean_name}"
            else:
                 # It's a distinct device (e.g. "Office Jarvis") without hardware IDs or wrapper suffixes.
                 # Give it a unique group to prevent room-wide merge.
                 group_key = f"entity:{eid}"
        
        # Add to Group
        # Infer Integration (Tentative - updated properly in refresh_devices, but good for local context)
        integration = "unknown"
        if "mass" in eid or attrs.get("app_id") == "music_assistant":
            integration = "music_assistant"
        elif "androidtv" in eid or "remote" in eid:
             integration = "androidtv_remote"
        elif "_chrome" in eid or "cast" in attrs.get("app_name", "").lower():
             integration = "cast"
             
        groups[group_key]["members"].append({
            "entity_id": eid,
            "friendly_name": e.get("friendly_name"),
            "domain": eid.split(".")[0],
            "integration": integration,
            "state": e.get("state"),
            "features": attrs.get("supported_features", 0),
            "attributes": attrs,
            "manufacturer": man,
            "model": mod
        })
        
        # Update Representative Friendly Name for the Group
        # Goal: "Office TV" is better than "Office TV Chrome" or "Office TV Remote"
        curr_name = groups[group_key]["friendly_name"]
        new_name = normalize_name(attrs.get("friendly_name", eid)).title()
        
        # Heuristic: Shorter normalized names are usually 'better' / more base names
        # e.g. "Office Tv" vs "Office Tv Chrome"
        if not curr_name or (len(new_name) < len(curr_name) and len(new_name) > 2):
             groups[group_key]["friendly_name"] = new_name

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
