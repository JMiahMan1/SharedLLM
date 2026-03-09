
import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.getcwd())

from app.domains.media.integrations.factory import IntegrationFactory
from app.domains.media.integrations.roku import RokuIntegration
from app.domains.media.integrations.androidtv import AndroidTVIntegration
from app.domains.media.integrations.standard import StandardIntegration

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("FinalVerifier")

async def test_roku_integration():
    print("\n[Test 1] Verifying RokuIntegration Power Methods")
    roku = RokuIntegration()
    
    # Check for turn_on
    if hasattr(roku, "turn_on") and callable(roku.turn_on):
        # Inspect code or behavior? Just existence is mostly what we added
        print("[PASS] RokuIntegration.turn_on exists.")
    else:
        print("[FAIL] RokuIntegration.turn_on MISSING.")
        
    # Check for turn_off
    if hasattr(roku, "turn_off") and callable(roku.turn_off):
        print("[PASS] RokuIntegration.turn_off exists.")
    else:
        print("[FAIL] RokuIntegration.turn_off MISSING.")

async def test_android_integration():
    print("\n[Test 2] Verifying AndroidTV Integration Loading")
    
    try:
        handler = IntegrationFactory.get_handler("androidtv")
        if isinstance(handler, AndroidTVIntegration):
             print(f"[PASS] Factory loaded AndroidTVIntegration for 'androidtv'.")
        else:
             print(f"[FAIL] Factory loaded {type(handler)} instead of AndroidTVIntegration.")
             
        # Verify it has standard turn_on (using inheritance or override)
        if hasattr(handler, "turn_on"):
             print("[PASS] AndroidTVIntegration has turn_on method.")
    except Exception as e:
        print(f"[FAIL] Factory crashed loading androidtv: {e}")

async def test_announcement_fix():
    print("\n[Test 3] Verifying Announcement Logic Fix (Factory Call)")
    
    # We are testing if IntegrationFactory.get_handler works for strings likely used in logic.py
    # logic.py uses: caps.get("integration", "standard") -> get_handler(integration_name)
    
    test_cases = ["androidtv", "roku", "standard", "cast", "unknown_junk"]
    
    for case in test_cases:
        try:
            handler = IntegrationFactory.get_handler(case)
            print(f"[PASS] get_handler('{case}') returned {type(handler).__name__}")
        except AttributeError:
             print(f"[FAIL] get_handler('{case}') raised AttributeError (Old bug?)")
        except Exception as e:
             print(f"[FAIL] get_handler('{case}') raised {e}")

async def main():
    await test_roku_integration()
    await test_android_integration()
    await test_announcement_fix()

    print("\nVERIFICATION COMPLETE.")

if __name__ == "__main__":
    asyncio.run(main())
