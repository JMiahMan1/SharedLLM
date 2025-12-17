
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
        
        clean_name = normalize_name(attrs.get("friendly_name", eid))
        if not clean_name: clean_name = "unknown"
        
        # Strategy 0: Super Unification (Man + Mod + Name Match)
        # This merges distinct HA Devices (e.g. Cast vs Remote) that share
        # the same hardware signature and same logical name (e.g. "Office").
        if man and mod and clean_name != "unknown":
             # "askey:sti6140d360:office"
             group_key = f"{man}:{mod}:{clean_name}".lower()
        
        # Strategy 1: Device ID (Strong - native HA grouping)
        elif did:
            group_key = f"device_id:{did}"
            
        # Strategy 2: Man/Model Only (Generic hardware grouping?)
        # Risky without name if user has multiple devices of same model.
        # Fallback to Name.
        
        # Strategy 3: Name Normalization (Legacy/Fallback)
        if not group_key:
            group_key = f"name:{clean_name}"
        
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
