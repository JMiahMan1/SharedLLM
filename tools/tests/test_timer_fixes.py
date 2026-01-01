
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

async def test_regression_fixes():
    print("--- STARTING REGRESSION VERIFICATION ---")

    # 1. Verify Intent Classification (Regex Override)
    from app.logic.intents.classifier import IntentClassifier
    
    queries = {
        "Pause the timer": "timer_pause",
        "Resume the timer": "timer_resume",
        "Cancel the timer": "timer_delete",
        "Pause the music": "pause_media",
        "Resume the movie": "media_play"
    }
    
    print("\n[STEP 1] Verifying Intent Routing...")
    for q, expected in queries.items():
        intent = IntentClassifier.apply_regex_override(q)
        if intent == expected:
            print(f"  [PASS] '{q}' -> {intent}")
        else:
            print(f"  [FAIL] '{q}' -> {intent} (Expected: {expected})")
            return False

    # 2. Verify Handler Argument Pass-through
    print("\n[STEP 2] Verifying Handler Argument Pass-through...")
    from app.logic.execution.registry import ActionDispatcher
    # Ensure handlers are registered
    import app.logic.execution.handlers 
    
    # Mock GlobalResources
    with patch("app.logic.execution.handlers.GlobalResources") as mock_resources, \
         patch("app.logic.execution.handlers.tool_timer_pause", new_callable=AsyncMock) as mock_pause:
        
        mock_resources.redis_client = MagicMock()
        mock_creds = {"user": "test_user"}
        
        # Dispatch 'timer_pause'
        await ActionDispatcher.dispatch("timer_pause", query="pause the timer", user_creds=mock_creds)
        
        # Verify tool_timer_pause was called with (query, user_creds, redis_client)
        try:
            mock_pause.assert_called_once_with("pause the timer", mock_creds, mock_resources.redis_client)
            print("  [PASS] tool_timer_pause received correct arguments.")
        except AssertionError as e:
            print(f"  [FAIL] tool_timer_pause argument mismatch: {e}")
            return False

    print("\n--- REGRESSION VERIFICATION COMPLETE: ALL LOGIC FIXES PROVEN ---")
    return True

if __name__ == "__main__":
    asyncio.run(test_regression_fixes())
