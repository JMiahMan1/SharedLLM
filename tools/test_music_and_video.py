#!/usr/bin/env python3
"""
Comprehensive test for Music Assistant and Video playback on all devices
"""
import requests
import json
import time

REMOTE_URL = "http://192.168.2.205:11435/api/chat"

def test_playback(test_name, query, expected_device):
    """Test playback on a specific device"""
    print(f"\n{'='*80}")
    print(f"Test: {test_name}")
    print(f"Query: {query}")
    print(f"Expected Device: {expected_device}")
    print(f"{'='*80}")
    
    try:
        response = requests.post(
            REMOTE_URL,
            json={"query": query},
            timeout=120
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data.get("message", {}).get("content", "")
            print(f"✓ Status: {response.status_code}")
            print(f"✓ Response: {content[:200]}...")  # Truncate long responses
            
            # Check if correct device is mentioned
            if expected_device.lower() in content.lower():
                print(f"✓ SUCCESS: Correct device '{expected_device}' targeted")
            else:
                print(f"✗ WARNING: Expected '{expected_device}' in response")
        else:
            print(f"✗ FAILED: HTTP {response.status_code}")
            print(f"   {response.text[:200]}")
            
    except Exception as e:
        print(f"✗ ERROR: {e}")
    
    # Delay between tests
    time.sleep(3)

def main():
    print("\n🎵 MUSIC & VIDEO PLAYBACK TEST SUITE")
    print("=" * 80)
    
    tests = [
        # Music Assistant tests
        ("Music - Office TV", "Play Brandon Lake on Office TV", "Office TV"),
        ("Music - Gracies TV", "Listen to Brandon Lake on Gracies TV", "Gracies TV"),
        
        # Video tests
        ("Video - Office TV", "Watch Landslide by Fleetwood Mac on Office TV", "Office TV"),
        ("Video - Gracies TV", "Watch Landslide by Fleetwood Mac on Gracies TV", "Gracies TV"),
    ]
    
    for test_name, query, expected_device in tests:
        test_playback(test_name, query, expected_device)
    
    print(f"\n{'='*80}")
    print("✓ Test Suite Complete")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
