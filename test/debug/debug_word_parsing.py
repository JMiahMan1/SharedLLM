import re
import dateparser
from datetime import datetime, timedelta

WORD_TO_NUM = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty': 40,
    'fifty': 50, 'sixty': 60
}

def convert_words_to_numbers(text):
    text = text.lower()
    # Hyphenated (twenty-five -> 25)
    for w1, v1 in WORD_TO_NUM.items():
        for w2, v2 in WORD_TO_NUM.items():
            if v1 >= 20 and v2 < 10:
                pattern = f"\\b{w1}-{w2}\\b"
                if re.search(pattern, text):
                    print(f"  -> Converting hyphenated '{w1}-{w2}' to {v1+v2}")
                    text = re.sub(pattern, str(v1 + v2), text)
    
    # Single words
    for word, value in WORD_TO_NUM.items():
        pattern = f"\\b{word}\\b"
        if re.search(pattern, text):
            print(f"  -> Converting word '{word}' to {value}")
            text = re.sub(pattern, str(value), text)
    return text

def test_query(query):
    print(f"\n--- Testing: '{query}' ---")
    
    # 1. Convert Words
    normalized = convert_words_to_numbers(query)
    print(f"Normalized: '{normalized}'")
    
    # 2. Regex Scan (The new logic)
    # Matches "30 seconds", "1 minute", "2 hours 5 minutes"
    duration_regex = r'(?:(\d+)\s*(?:hour|hr)s?)?\s*(?:(\d+)\s*(?:minute|min)s?)?\s*(?:(\d+)\s*(?:second|sec)s?)?'
    
    # Clean prefix for regex
    clean_input = re.sub(r'^\s*[\d\.\s]*(?:can you|please|i want to|start|set|create|add|a|an|\s+)+', '', normalized).strip()
    print(f"Cleaned for Regex: '{clean_parse_input}'") # Note: variable name in print matches concept
    
    match = re.match(duration_regex, clean_input)
    if match and any(match.groups()):
        h, m, s = match.groups()
        print(f"SUCCESS: Found duration -> H:{h or 0} M:{m or 0} S:{s or 0}")
    else:
        print("FAIL: No duration pattern found.")

if __name__ == "__main__":
    test_query("Set a 30-second egg timer")
    test_query("Set a two minute timer")
    test_query("Set a twenty-five minute timer")
