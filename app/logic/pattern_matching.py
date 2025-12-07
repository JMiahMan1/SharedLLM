"""
Multi-device pattern matching utilities for batch device control.
Supports patterns like "even numbered lights", "lights 1-4", "all lights", etc.
"""
import re
from typing import List, Tuple, Optional, Dict
import logging

log = logging.getLogger(__name__)


def detect_number_pattern(query: str) -> Tuple[Optional[str], Optional[Dict]]:
    """
    Detect if query contains a number pattern for multi-device control.
    
    Returns:
        Tuple of (pattern_type, pattern_data) where:
        - pattern_type: 'even', 'odd', 'range', 'list', 'all', or None
        - pattern_data: Dict with pattern-specific info
    """
    q_low = query.lower()
    
    # Pattern 1: Even numbers
    if re.search(r'\b(even\s+number|even-number|even\s+numbered)\b', q_low):
        log.debug(f"[PATTERN] Detected: even numbers")
        return ('even', {})
    
    # Pattern 2: Odd numbers
    if re.search(r'\b(odd\s+number|odd-number|odd\s+numbered)\b', q_low):
        log.debug(f"[PATTERN] Detected: odd numbers")
        return ('odd', {})
    
    # Pattern 3: Range (e.g., "1-4", "1 through 4")
    range_match = re.search(r'(\d+)\s*(?:-|through|to)\s*(\d+)', query)
    if range_match:
        min_num = int(range_match.group(1))
        max_num = int(range_match.group(2))
        log.debug(f"[PATTERN] Detected: range {min_num}-{max_num}")
        return ('range', {'min': min_num, 'max': max_num})
    
    # Pattern 4: Explicit list (e.g., "1, 2, and 3")
    list_match = re.findall(r'\b(\d+)\b(?:\s*,\s*|\s+and\s+)', query)
    if list_match:
        numbers = [int(n) for n in list_match]
        final_match = re.search(r'(?:and|,)\s+(\d+)\b(?!.*\d)', query)
        if final_match:
            numbers.append(int(final_match.group(1)))
        if len(numbers) > 1:
            log.debug(f"[PATTERN] Detected: list {numbers}")
            return ('list', {'numbers': numbers})
    
    # Pattern 5: All/Every
    if re.search(r'\b(all|every)\b', q_low):
        log.debug(f"[PATTERN] Detected: all")
        return ('all', {})
    
    return (None, None)


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
    """Filter entities based on number pattern using friendly names."""
    if pattern_type == 'all':
        log.info(f"[PATTERN] Returning all {len(entities)} entities")
        return entities
    
    matching = []
    for entity_id, integration in entities:
        friendly_name = friendly_names.get(entity_id, entity_id)
        num = extract_number_from_friendly_name(friendly_name)
        
        if num is None:
            continue
        
        matched = False
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
            log.debug(f"[PATTERN] Matched: {friendly_name} (#{num})")
            matching.append((entity_id, integration))
    
    log.info(f"[PATTERN] Filtered {len(entities)} → {len(matching)} matching '{pattern_type}'")
    return matching
