
import asyncio
import logging
import sys
import os

# Setup paths
sys.path.append(os.getcwd())

from app.settings import load_resources
from app.domains.shared import execute_ha_service
# from app.domains.media.integrations.cast import CastIntegration -- Removed to avoid circular import

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("VERIFY_CLICK")

# Mock Credentials (from settings or env)
# The container has HA_TOKEN, app.settings maps it to HA_ENV_TOKEN.
USER_CREDS = {"ha_token": os.environ.get("HA_TOKEN", "")}

async def main():
    log.info("--- Starting Youtube Click Verification ---")
    await load_resources()  # type: ignore[misc]

    entity_id = "media_player.office_tv_chrome"
    # Found via list_remotes.py
    remote_id = "remote.office_tv_remote" 

    # 1. Launch YouTube App (using our known working payload)
    log.info(f"Step 1: Launching YouTube on {entity_id}...")
    
    # We can manually reconstruct the service call to ensure we test the exact "App Mode" payload
    import json
    cast_payload = {
        "app_name": "youtube",
        "media_id": "HF6LSbMKvrw" # Fireplace video
    }
    
    await execute_ha_service(
         "media_player", 
         "play_media", 
         entity_id, 
         USER_CREDS, 
         {
             "media_content_id": json.dumps(cast_payload),
             "media_content_type": "cast"
         }, 
         None
    )
    
    log.info("Step 2: Waiting 15s for App Load & Profile Screen...")
    await asyncio.sleep(15)

    # 2. Try to Send Command
    # Common commands: 'select', 'center', 'enter', 'confirm'
    cmd = "center"
    log.info(f"Step 3: Sending '{cmd}' to {remote_id}...")

    try:
        await execute_ha_service(
            "remote",
            "send_command",
            remote_id,
            USER_CREDS,
            {"command": cmd},
            None
        )
        log.info("Command sent! Check TV to see if profile was selected.")
    except Exception as e:
        log.error(f"Failed to send command: {e}")

if __name__ == "__main__":
    asyncio.run(main())
