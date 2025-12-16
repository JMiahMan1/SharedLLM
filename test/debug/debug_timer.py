# test/debug_timer_parsing.py
import re
import dateparser
from datetime import datetime, timedelta

def debug_parsing(query):
    print(f"\n{'='*40}")
    print(f"Testing Query: '{query}'")
    print(f"{'='*40}")
    
    now = datetime.now()
    query_lower = query.lower()

    # --- 1. Clean introductory words/filler ---
    # (Matches logic in timer_ops.py)
    clean_parse_input = re.sub(
        r'^\s*[\d\.\s]*(?:can you|please|i want to|start|set|create|add|a|an|\s+)+', '', query_lower, flags=re.IGNORECASE
    ).strip()
    
    print(f"Cleaned Input: '{clean_parse_input}'")

    # --- 2. Robust Duration Extraction (Scan Anywhere) ---
    hours = 0
    minutes = 0
    seconds = 0
    found_duration = False
    
    # Extract Hours
    h_match = re.search(r'(\d+)\s*(?:hours?|hrs?)', query_lower)
    if h_match:
        hours = int(h_match.group(1))
        found_duration = True
        print(f"  -> Found Hours: {hours}")
        
    # Extract Minutes
    m_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', query_lower)
    if m_match:
        minutes = int(m_match.group(1))
        found_duration = True
        print(f"  -> Found Minutes: {minutes}")
        
    # Extract Seconds (handles "30-second" via -?)
    s_match = re.search(r'(\d+)\s*-?\s*(?:seconds?|secs?)', query_lower)
    if s_match:
        seconds = int(s_match.group(1))
        found_duration = True
        print(f"  -> Found Seconds: {seconds}")

    if found_duration:
        expires_at = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        print(f"SUCCESS (Regex): Calculated Expiry: {expires_at.strftime('%I:%M:%S %p')}")
    else:
        print("Regex Scan: NO DURATION FOUND. Trying Dateparser...")
        
        # --- 3. Fallback to Dateparser ---
        # Remove confusing keywords
        dp_input = re.sub(r'\b(timer|alarm|wake me|remind me|set|start|create|add)\b', '', query_lower, flags=re.IGNORECASE)
        
        dt = dateparser.parse(
            dp_input,
            settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
        )
        if dt:
            print(f"SUCCESS (Dateparser): Matched absolute time: {dt.strftime('%I:%M:%S %p')}")
        else:
            print("FAILURE: Could not parse time.")

if __name__ == "__main__":
    # Test cases that were failing in your old script
    debug_parsing("Set a 30-second egg timer")
    debug_parsing("Start a timer for 10 minutes")
    debug_parsing("Set an alarm for 6am tomorrow")
    debug_parsing("Remind me in 5 minutes to check the oven")
