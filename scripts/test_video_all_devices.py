#!/usr/bin/env python3
"""
Comprehensive test for video playback on all devices
"""
import time

import requests

REMOTE_URL = "http://ai.local:11435/api/chat"

def test_video_playback(device_name, query):
    """Test video playback on a specific device"""
    print(f"\n{'='*80}")
    print(f"Testing: {query}")
    print(f"Device: {device_name}")
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
            print(f"✓ Response: {content}")

            # Check if correct device is mentioned
            if device_name.lower() in content.lower():
                print(f"✓ SUCCESS: Correct device '{device_name}' targeted")
            else:
                print(f"✗ WARNING: Expected '{device_name}' but got: {content}")
        else:
            print(f"✗ FAILED: HTTP {response.status_code}")
            print(f"   {response.text}")

    except Exception as e:
        print(f"✗ ERROR: {e}")

    # Small delay between tests
    time.sleep(2)

def main():
    print("\n🎬 VIDEO PLAYBACK TEST SUITE")
    print("Query: 'Watch Landslide by Fleetwood Mac on [DEVICE]'\n")

    # Test both devices
    devices = [
        ("Office TV", "Watch Landslide by Fleetwood Mac on Office TV"),
        ("Gracies TV", "Watch Landslide by Fleetwood Mac on Gracies TV")
    ]

    for device_name, query in devices:
        test_video_playback(device_name, query)

    print(f"\n{'='*80}")
    print("✓ Test Suite Complete")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
