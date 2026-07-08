import sys
import time

import pychromecast  # pyright: ignore[reportMissingImports]

TARGET_IP = "192.168.2.240"  # Found via scan_cast_ips.py
VIDEO_URL = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
MIME_TYPE = "video/mp4"

print(f"--- Attempting Cast to {TARGET_IP} (Host Mode) ---")

try:
    # 1. Connect directly to IP (Bypassing Discovery)
    print(f"Connecting to {TARGET_IP}...")
    cast = pychromecast.Chromecast(TARGET_IP)
    cast.wait()
    print(f"Connected to: {cast.cast_info.friendly_name} ({cast.model_name})")

    # 2. Launch Media
    print("Launching media...")
    mc = cast.media_controller
    mc.play_media(VIDEO_URL, MIME_TYPE)
    mc.block_until_active()

    print(f"Media State: {mc.status.player_state}")

    # 3. Wait a bit
    time.sleep(5)

    # 4. Check status again
    print("Updating status...")
    mc.update_status()
    print(f"Current Time: {mc.status.current_time}")
    print(f"Media State: {mc.status.player_state}")

    if mc.status.player_state in ['PLAYING', 'BUFFERING']:
        print("SUCCESS: Video is playing/buffering.")
    else:
        print("WARNING: Video state is idle/unknown.")

    # 5. Stop (optional, maybe leave it playing as proof?)
    # mc.stop()

except Exception as e:
    print(f"FAILURE: {e}")
    sys.exit(1)
