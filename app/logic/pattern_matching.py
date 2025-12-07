"""
Multi-device pattern matching utilities for batch device control.
Supports patterns like "even numbered lights", "lights 1-4", "all lights", "North Bedroom", "Upstairs", etc.
"""
import re
from typing import List, Tuple, Optional, Dict
import logging

log = logging.getLogger(__name__)

# Pattern Definitions
PATTERNS = {
    'even': r'\b(even\s+number|even-number|even\s+numbered)\b',
    'odd': r'\b(odd\s+number|odd-number|odd\s+numbered)\b',
    'all': r'\b(all|every)\b',
    'location': r'\b(upstairs|downstairs|inside|outside|front|back|yard|patio|basement|attic|garage)\b',
    'direction': r'\b(north|south|east|west|left|right|center|middle)\b',
    'plural': r'\b(lights|lamps|bulbs|switches|fans|blinds|shades|speakers|players|tvs)\b'
}

def detect_entity_pattern(query: str) -> Tuple[Optional[str], Optional[Dict]]:
    """
    Detect if query contains a pattern for multi-device or specific location control.
    
    Returns:
        Tuple of (pattern_type, pattern_data) where:
        - pattern_type: 'even', 'odd', 'range', 'list', 'all', 'location', 'direction', or None
        - pattern_data: Dict with pattern-specific info
    """
    q_low = query.lower()
    
    # Check simple regex patterns
    for p_type, p_regex in PATTERNS.items():
        match = re.search(p_regex, q_low)
        if match:
             val = match.group(1)
             log.debug(f"[PATTERN] Detected {p_type}: {val}")
             return (p_type, {'value': val})

    # Pattern: Range (e.g., "1-4", "1 through 4")
    range_match = re.search(r'(\d+)\s*(?:-|through|to)\s*(\d+)', query)
    if range_match:
        min_num = int(range_match.group(1))
        max_num = int(range_match.group(2))
        log.debug(f"[PATTERN] Detected: range {min_num}-{max_num}")
        return ('range', {'min': min_num, 'max': max_num})
    
    # Pattern: Explicit list (e.g., "1, 2, and 3")
    # We look for a sequence of numbers separated by commas or 'and'
    list_match = re.findall(r'\b(\d+)\b', query)
    if len(list_match) > 1 and (',' in query or ' and ' in query):
        numbers = [int(n) for n in list_match]
        log.debug(f"[PATTERN] Detected: list {numbers}")
        return ('list', {'numbers': numbers})
    
    return (None, None)

# Alias for backward compatibility if needed, though we should update callers
detect_number_pattern = detect_entity_pattern

def extract_number_from_friendly_name(friendly_name: str) -> Optional[int]:
    """Extract trailing number from friendly name like 'Kitchen Light 2' -> 2"""
    match = re.search(r'\s(\d+)\s*(?:switch|light|lamp|bulb)?$', friendly_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def filter_entities_by_pattern(
    entities: List[Tuple[str, str]], 
    pattern_type: str,
    pattern_data: Dict,
    friendly_names: Dict[str, str]
) -> List[Tuple[str, str]]:
    """Filter entities based on detected pattern using friendly names."""
    
    if pattern_type == 'all':
        log.info(f"[PATTERN] 'All' detected, returning all {len(entities)} entities")
        return entities
    
    matching = []
    target_val = pattern_data.get('value', '').lower()

    # Simple singularization for plural matching
    if pattern_type == 'plural':
        # Remove trailing 's' roughly, or map specific cases if needed
        if target_val.endswith('s'):
            target_val = target_val[:-1]
    
    for entity_id, integration in entities:
        friendly_name = friendly_names.get(entity_id, entity_id)
        fn_low = friendly_name.lower()
        
        matched = False
        
        # Location / Direction / Plural Logic
        # We match if the keyword (or singularized keyword) is in the name
        if pattern_type in ['location', 'direction', 'plural']:
            if target_val in fn_low:
                matched = True
        
        # Number Logic
        else:
            num = extract_number_from_friendly_name(friendly_name)
            if num is not None:
                if pattern_type == 'even' and num % 2 == 0:
                    matched = True
                elif pattern_type == 'odd' and num % 2 == 1:
                    matched = True
                elif pattern_type == 'range':
                    if pattern_data['min'] <= num <= pattern_data['max']:
                        matched = True
                elif pattern_type == 'list' and num in pattern_data['numbers']:
                    matched = True
        
        if matched:
            log.debug(f"[PATTERN] Matched: {friendly_name} (Type: {pattern_type})")
            matching.append((entity_id, integration))
    
    log.info(f"[PATTERN] Filtered {len(entities)} → {len(matching)} matching '{pattern_type}'")
    return matching
