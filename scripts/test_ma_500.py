
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock

sys.path.append(os.getcwd())

# Mock the ops module BEFORE importing the integration
sys.modules["app.logic.music_assistant_ops"] = MagicMock()
from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
from app.logic import music_assistant_ops

async def main():
    print("Testing MA 500 Error Transformation...")
    
    # Setup Mock
    # Simulate a 500 error response from the ops layer
    error_response = {"status": "FAILURE", "message": "Failed to play media... Last error: HTTP 500: 500 Internal Server Error"}
    music_assistant_ops.play_media = AsyncMock(return_value=error_response)
    
    integration = MusicAssistantIntegration()
    
    # Test Query
    query = "playbrand at lake"
    device = "Office TV"
    
    print(f"Executing Query: '{query}'")
    result = await integration.play_media("media.test", query, "music", {}, device_name=device)
    
    print("\nResult:")
    print(result)
    
    # Verification
    if result["status"] == "FAILURE" and "couldn't find any music" in result["message"]:
        print("\nPASS: 500 Error successfully transformed to 'Not Found' message.")
    else:
        print(f"\nFAIL: Unexpected result message: {result['message']}")
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())
