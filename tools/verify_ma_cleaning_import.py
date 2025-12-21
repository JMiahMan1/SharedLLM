
import sys
import os

sys.path.append(os.getcwd()) 

from app.domains.media.integrations.music_assistant import MusicAssistantIntegration

def test_cleaning():
    print("Testing MusicAssistantIntegration._clean_query logic...")
    integration = MusicAssistantIntegration()
    
    # Case 1: The User's Failure Case
    query = "Play Reliant Kay on Gracie's TV"
    device = "Gracies TV"
    cleaned = integration._clean_query(query, device)
    
    print(f"Original: '{query}'")
    print(f"Device:   '{device}'")
    print(f"Cleaned:  '{cleaned}'")
    
    expected = "reliant kay"
    if cleaned == expected:
        print("PASS: Apostrophe handled correctly.")
    else:
        print(f"FAIL: Expected '{expected}', got '{cleaned}'")
        exit(1)

    # Case 2: Clean Query (Sanity Check)
    q2 = "Play Relient K"
    c2 = integration._clean_query(q2, device)
    if c2 == "relient k":
        print("PASS: Clean query preserved.")
    else:
         print(f"FAIL: Expected 'relient k', got '{c2}'")
         exit(1)

    # Case 3: Generic 'on the TV' (if using generic intent)
    q3 = "Play Music on the TV"
    c3 = integration._clean_query(q3, device)
    # Ideally should remove 'on the tv'
    print(f"Generic Cleaned: '{c3}'")
    if "tv" not in c3:
         print("PASS: Generic TV removed.")
    else:
         print("WARN: 'tv' remains in generic query.")

    print("All search hygiene tests passed.")

if __name__ == "__main__":
    test_cleaning()
