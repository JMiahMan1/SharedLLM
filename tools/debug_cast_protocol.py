
import time
import logging
import uuid
import sys

# Ensure library imports work
try:
    from pychromecast.socket_client import SocketClient
    from pychromecast.discovery import HostServiceInfo
    from pychromecast.controllers.media import MediaController
    from pychromecast.controllers.receiver import ReceiverController
    from casttube import YouTubeSession
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("CAST_DEBUG")

def main():
    log.info("--- Direct IP Check ---")
    target_ips = ["192.168.2.159", "192.168.2.238", "192.168.2.240"]

    for ip in target_ips:
        try:
            log.info(f"Checking {ip}...")
            
            # Construct Service Info for the client
            # Signature validated via inspect: services={HostServiceInfo}
            h_info = HostServiceInfo(
                host=ip, 
                port=8009, 
                uuid=str(uuid.uuid4()), 
                model_name="Chromecast", 
                friendly_name="Unknown"
            )
            
            # Instantiate SocketClient
            client = SocketClient(
                services={h_info},
                zconf=None, 
                cast_type='cast'
            )
            
            client.connect()
            
            # Receiver Controller to check name
            receiver = ReceiverController(cast_type='cast')
            client.register_handler(receiver)
            
            # Wait for status
            time.sleep(1)
            receiver.update_status()
            
            fname = receiver.status.friendly_name if receiver.status else "Unknown"
            log.info(f"  > Connected. FriendlyName: {fname}")
            
            if "Office" in fname:
                log.info(f"MATCH: Found Office TV at {ip}")
                
                # Media Controller
                media = MediaController()
                client.register_handler(media)
                time.sleep(1) 
                
                # CastTube Playback
                log.info("--- Testing casttube (YouTube) ---")
                yt = YouTubeSession(client)
                video_id = "HF6LSbMKvrw"
                log.info(f"Playing Video ID: {video_id}...")
                yt.play_video(video_id)
                
                for i in range(10):
                    time.sleep(2)
                    media.update_status()
                    state = media.status.player_state if media.status else "Unknown"
                    log.info(f"[{i}] Status: {state}")
                    if state == "PLAYING":
                        log.info("SUCCESS: Video is playing!")
                        # Keep it playing for a moment to verify
                        break
                
                client.disconnect()
                break
            
            client.disconnect()
            
        except Exception as e:
            log.error(f"Failed for {ip}: {e}")
            try: client.disconnect() 
            except: pass

if __name__ == "__main__":
    main()
