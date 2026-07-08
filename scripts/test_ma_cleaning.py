
import re


# Mocking the _clean_query method logic for testing to avoid importing entire app context
def clean_query_current(query: str, device_name: str = "") -> str:
    """Current implementation of _clean_query from music_assistant.py"""
    clean = query.lower()

    # Remove device name if known
    if device_name:
        d_clean = device_name.lower().strip()
        # Try to remove "on [device_name]" first
        clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?" + re.escape(d_clean) + r"\b", " ", clean)
        # Remove just the device name
        clean = clean.replace(d_clean, " ")

    # Remove common MA keywords
    clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean)
    # Remove actions
    clean = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean)

    # Remove "on X" pattern at end
    clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?(office|living|bedroom|kitchen|garage|patio|tv|speaker|soundbar).*$", "", clean)

    # Remove "the" if standalone
    clean = re.sub(r"\bthe\b", "", clean)

    # Remove punctuation
    clean = re.sub(r"[^\w\s]", "", clean)

    return re.sub(r'\s+', ' ', clean).strip()

def test_apostrophe_cleaning():
    original_query = "Play Reliant Kay on Gracie's TV"
    device_name = "Gracies TV" # Logic resolves to entity friendly_name which often lacks 's

    print(f"Testing Query: '{original_query}'")
    print(f"Device Name: '{device_name}'")

    # Case 1: Apostrophe handling (Existing)
    cleaned = clean_query_current(original_query, device_name)
    print(f"Cleaned Query (Current): '{cleaned}'")

    # Case 2: Fuzzy/Misspelled Device Name (User Reported Issue)
    # User said "Grace's TV", System has "Gracies TV"
    print("\n--- Testing Fuzzy Mismatch ---")
    query_fuzzy = "Play Reliant K on Grace's TV"
    device_fuzzy = "Gracies TV"

    cleaned_fuzzy = clean_query_current(query_fuzzy, device_fuzzy)
    print(f"Original: '{query_fuzzy}'")
    print(f"Device:   '{device_fuzzy}'")
    print(f"Cleaned:  '{cleaned_fuzzy}'")

    expected_fuzzy = "reliant k"
    if cleaned_fuzzy == expected_fuzzy:
        print("PASS: Fuzzy device name stripped.")
    else:
        print(f"FAIL: Expected '{expected_fuzzy}', got '{cleaned_fuzzy}'")
        # We expect this to fail with current logic


if __name__ == "__main__":
    test_apostrophe_cleaning()
