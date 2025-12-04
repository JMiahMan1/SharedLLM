import sys
import os
import asyncio
import pytest

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from logic.alarm_ops import tool_alarm_set, tool_alarm_list, tool_alarm_delete
from settings import GlobalResources

async def test_alarms():
    print("--- Testing Alarm Functionality ---")
    
    # 1. Test Set Alarm
    print("1. Testing Set Alarm...")
    res = await tool_alarm_set("Set an alarm for 5 seconds")
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "alarm set" in res["message"].lower()
    assert len(GlobalResources.alarms) == 1
    
    # 2. Test List Alarms
    print("\n2. Testing List Alarms...")
    res = await tool_alarm_list()
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "active alarms" in res["message"].lower()
    
    # 3. Test Delete Alarm
    print("\n3. Testing Delete Alarm...")
    res = await tool_alarm_delete("Cancel all alarms")
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "cancelled" in res["message"].lower()
    assert len(GlobalResources.alarms) == 0
    
    print("\n--- All Alarm Tests Passed ---")

if __name__ == "__main__":
    asyncio.run(test_alarms())
