
import sys
import os

# Add project root to path
sys.path.append('/') 

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

    print("All search hygiene tests passed.")

if __name__ == "__main__":
    test_cleaning()
