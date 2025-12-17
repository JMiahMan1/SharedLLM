
import asyncio
from app.settings import get_user_creds

# Mock functions to simulate get_entity_state logic roughly without actual API calls if possible,
# or just use the real logic by importing from app.
# Since we deployed the code, we can't easily unit test the deployed code from here without full env.
# But we can test the *logic* if we copy it, or run a trace on the server.

# We will run a script that imports the app logic (which is in current dir) and runs the routing block.
import logging
logging.basicConfig(level=logging.INFO)

async def check_routing():
    # Setup context
    import sys
    sys.path.append(".")
    from app.logic import media_ops
    
    # Mock services
    media_ops.get_entity_state = mock_get_state
    media_ops.execute_ha_service = mock_execute
    
    user_creds = {"user_id": "test", "ha_token": "dummy_token"}
    
    print("--- Test 1: Play Music on TV Entity (Should Swap to Speaker) ---")
    # Entity: media_player.office_tv (TV)
    # Query: "Play music"
    # Expected: Redirect to media_player.office_tv_chrome_2
    
    await media_ops.handle_media_command("play_media", "media_player.office_tv", "Play music", user_creds, None, None)
    
    print("\n--- Test 2: Play Video on Speaker Entity (Should Swap to TV) ---")
    # Entity: media_player.office_tv_chrome_2 (Speaker)
    # Query: "Watch video"
    # Expected: Redirect to media_player.office_tv
    
    await media_ops.handle_media_command("play_media", "media_player.office_tv_chrome_2", "Watch video", user_creds, None, None)

    print("\n--- Test 3: Turn Off Speaker Entity (Should Swap to TV) ---")
    await media_ops.handle_media_command("turn_off", "media_player.office_tv_chrome_2", "Turn off", user_creds, None, None)

# Mocks
async def mock_get_state(entity_id, creds):
    # Simulate states based on naming
    if "office_tv" in entity_id:
        if "chrome" in entity_id or "cast" in entity_id:
            return "idle" # Speaker
        return "on" # TV
    return "unknown"

async def mock_execute(domain, service, entity_id, creds, data, redis):
    print(f"EXECUTE: {domain}.{service} -> {entity_id} | Data: {data}")
    return {"status": "SUCCESS"}

if __name__ == "__main__":
    asyncio.run(check_routing())
