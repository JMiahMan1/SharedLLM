
import asyncio
import logging
from unittest.mock import MagicMock, patch
import sys
import os

# Add app to path
sys.path.append(os.getcwd())

from app.domains.media.integrations.roku import RokuIntegration
from app.domains.media.integrations.media_assistant_roku import RokuMediaAssistantIntegration

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("RokuVerifier")

async def test_integration_power_logic(integration_cls, name):
    print(f"\n--- Testing {name} Power Logic ---")
    
    # Mock dependencies
    integration = integration_cls()
    
    # Mock execute_ha_service to capture calls
    with patch("app.domains.media.integrations." + ("roku" if "Assistant" not in name else "media_assistant_roku") + ".execute_ha_service") as mock_exec:
        mock_exec.return_value = {"status": "SUCCESS"}
        
        # Test Data
        entity_id = "media_player.office_roku"
        user_creds = {"token": "fake"}
        
        # Mock _get_roku_remote to return a specific remote
        integration._get_roku_remote = MagicMock(return_value=asyncio.Future())
        integration._get_roku_remote.return_value.set_result("remote.office_roku")
        
        # ACT: Call turn_on
        await integration.turn_on(entity_id, user_creds)
        
        # ASSERT: Verify Sequence
        calls = mock_exec.call_args_list
        
        # Check 1: Standard Turn On
        has_turn_on = any(c[0][1] == "turn_on" and c[0][0] == "media_player" for c in calls)
        print(f"[{'PASS' if has_turn_on else 'FAIL'}] Standard 'media_player.turn_on' called")
        
        # Check 2: Explicit PowerOn
        has_power_on = any(
            c[0][0] == "remote" and 
            c[0][1] == "send_command" and 
            c[0][4].get("command") == "PowerOn" 
            for c in calls
        )
        print(f"[{'PASS' if has_power_on else 'FAIL'}] Explicit 'remote.send_command(PowerOn)' called")
        
        # Check 3: Home Command
        has_home = any(
            c[0][0] == "remote" and 
            c[0][1] == "send_command" and 
            c[0][4].get("command") == "Home" 
            for c in calls
        )
        print(f"[{'PASS' if has_home else 'FAIL'}] Wake-up 'remote.send_command(Home)' called")

async def main():
    await test_integration_power_logic(RokuIntegration, "RokuIntegration (Video)")
    await test_integration_power_logic(RokuMediaAssistantIntegration, "RokuMediaAssistantIntegration (Music)")

if __name__ == "__main__":
    asyncio.run(main())
