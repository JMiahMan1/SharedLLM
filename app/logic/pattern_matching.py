"""
Multi-device pattern matching utilities for batch device control.
Supports patterns like "even numbered lights", "lights 1-4", "all lights", "North Bedroom", "Upstairs", etc.
"""
import re
from typing import List, Tuple, Optional, Dict
import logging

log = logging.getLogger(__name__)

# Basic Regex Definitions
REGEX_EVEN = r'\b(even\s+number|even-number|even\s+numbered)\b'
REGEX_ODD = r'\b(odd\s+number|odd-number|odd\s+numbered)\b'
REGEX_ALL = r'\b(all|every)\b'
REGEX_DIRECTION = r'\b(north|south|east|west|left|right|center|middle)\b'
REGEX_PLURAL = r'\b(lights|lamps|bulbs|switches|fans|blinds|shades|speakers|players|tvs)\b'

# Dynamic Location Detection (Keywords to ignore if caught by dynamic regex)
LOCATION_STOPWORDS = {
    'turn', 'toggle', 'switch', 'flip', 'set', 'change', 'make', 'open', 'close',
    'on', 'off', 'up', 'down', 'the', 'my', 'a', 'an', 'some', 'any', 'all', 'every',
    'lights', 'fans', 'switches', 'blinds', 'speakers', 'please'
}

def detect_entity_pattern(query: str) -> List[Tuple[str, Dict]]:
    """
    Detect ONE OR MORE patterns for multi-device or specific location control.
    
    Returns:
        List of tuples [(pattern_type, pattern_data), ...]
        - pattern_type: 'even', 'odd', 'range', 'list', 'all', 'location', 'direction', 'plural'
        - pattern_data: Dict with pattern-specific info
    """
    q_low = query.lower()
    detected_patterns = []
    
    # 1. Check Keywords (All, Direction, Plural)
    if re.search(REGEX_ALL, q_low):
        detected_patterns.append(('all', {}))
        
    directional = re.search(REGEX_DIRECTION, q_low)
    if directional:
        detected_patterns.append(('direction', {'value': directional.group(1)}))
        
    plural = re.search(REGEX_PLURAL, q_low)
    if plural:
         detected_patterns.append(('plural', {'value': plural.group(1)}))

    # 2. Dynamic Location Detection
    # Case A: "in [Location]" (e.g. "lights in the kitchen")
    loc_in = re.search(r'\b(in|at)\s+(?:the\s+)?([a-z0-9\s]+)\b', q_low)
    location_val = None
    
    if loc_in:
        # Avoid capturing "in range" or similar if we add other logic
        candidate = loc_in.group(2).strip()
        if candidate not in LOCATION_STOPWORDS:
            location_val = candidate

    # Case B: "[Location] [Plural]" (e.g. "kitchen lights")
    # Only if we haven't found a location yet
    if not location_val:
        # Look for word preceding a plural keyword
        # We search specifically for the plural keyword found earlier, or general plural regex
        plural_word = plural.group(1) if plural else r'(?:lights|fans|switches|blinds|speakers)'
        loc_mod = re.search(r'\b([a-z0-9]+)\s+' + plural_word, q_low)
        if loc_mod:
            candidate = loc_mod.group(1).strip()
            if candidate not in LOCATION_STOPWORDS and candidate not in ['even', 'odd', 'all']:
                 location_val = candidate

    if location_val:
        log.debug(f"[PATTERN] Detected location: {location_val}")
        detected_patterns.append(('location', {'value': location_val}))


    # 3. Number Logic (Mutually exclusive usually)
    # Even/Odd
    if re.search(REGEX_EVEN, q_low):
        detected_patterns.append(('even', {}))
    elif re.search(REGEX_ODD, q_low):
        detected_patterns.append(('odd', {}))
    
    # Range (e.g., "1-4")
    range_match = re.search(r'(\d+)\s*(?:-|through|to)\s*(\d+)', query)
    if range_match:
        min_num = int(range_match.group(1))
        max_num = int(range_match.group(2))
        detected_patterns.append(('range', {'min': min_num, 'max': max_num}))
    
    # Explicit list (e.g., "1, 2, and 3")
    list_match = re.findall(r'\b(\d+)\b', query)
    if len(list_match) > 1 and (',' in query or ' and ' in query):
        numbers = [int(n) for n in list_match]
        detected_patterns.append(('list', {'numbers': numbers}))
    
    return detected_patterns

# Alias for backward compatibility if needed, though we should update callers
detect_number_pattern = detect_entity_pattern

def extract_number_from_friendly_name(friendly_name: str) -> Optional[int]:
    """Extract trailing number from friendly name like 'Kitchen Light 2' -> 2"""
    match = re.search(r'\s(\d+)\s*(?:switch|light|lamp|bulb)?$', friendly_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def filter_entities_by_pattern(
    entities: List[Tuple[str, str, Dict]], 
    patterns: List[Tuple[str, Dict]]
) -> List[Tuple[str, str]]:
    """
    Filter entities based on detected patterns using metadata (friendly_name, area_name).
    Applies INTERSECTION (AND) logic: Entity must match ALL detected patterns.
    """
    if not patterns:
        return [(e[0], e[1]) for e in entities]

    matching = []
    
    # Check if 'all' is present to adjust strictness
    has_all = any(p[0] == 'all' for p in patterns)
    
    for entity_id, integration, metadata in entities:
        friendly_name = metadata.get("friendly_name", entity_id)
        area_name = metadata.get("area_name", "")
        domain = metadata.get("domain", entity_id.split('.')[0])
        
        fn_low = friendly_name.lower()
        area_low = area_name.lower()
        
        matches_all = True
        
        for p_type, p_data in patterns:
            target_val = p_data.get('value', '').lower()
            
            # Singularization for plural pattern matching
            if p_type == 'plural' and target_val.endswith('s'):
                target_val = target_val[:-1]

            is_match = False
            
            if p_type == 'all':
                is_match = True # "All" is a directive, not a filter for the item itself
                
            elif p_type == 'location':
                # Match Name OR Area
                if target_val in fn_low or target_val in area_low:
                    is_match = True
                    
            elif p_type == 'direction':
                 if target_val in fn_low:
                    is_match = True
                    
            elif p_type == 'plural':
                # If "All" is present, we relax the name check if the domain matches the intent
                # e.g. "All Kitchen Lights" -> matches domain 'light' even if name is 'Table Lamp'
                if has_all:
                    # Map plural keywords to domains broadly
                    if 'light' in target_val and domain == 'light': is_match = True
                    elif 'fan' in target_val and domain == 'fan': is_match = True
                    elif 'switch' in target_val and domain == 'switch': is_match = True
                    elif 'blind' in target_val or 'shade' in target_val:
                        if domain in ['cover', 'blind']: is_match = True
                    elif 'speaker' in target_val and domain == 'media_player': is_match = True
                
                # Default text match (always fallback to this)
                if not is_match and target_val in fn_low:
                    is_match = True
                    
            elif p_type == 'even':
                num = extract_number_from_friendly_name(friendly_name)
                if num is not None and num % 2 == 0: is_match = True
                
            elif p_type == 'odd':
                num = extract_number_from_friendly_name(friendly_name)
                if num is not None and num % 2 == 1: is_match = True
                
            elif p_type == 'range':
                num = extract_number_from_friendly_name(friendly_name)
                if num is not None and p_data['min'] <= num <= p_data['max']:
                    is_match = True
                    
            elif p_type == 'list':
                num = extract_number_from_friendly_name(friendly_name)
                if num is not None and num in p_data['numbers']:
                    is_match = True
            
            if not is_match:
                matches_all = False
                break
        
        if matches_all:
            log.debug(f"[PATTERN] Matched: {friendly_name} (Patterns: {[p[0] for p in patterns]})")
            matching.append((entity_id, integration))
    
    log.info(f"[PATTERN] Filtered {len(entities)} → {len(matching)} matching patterns")
    return matching
