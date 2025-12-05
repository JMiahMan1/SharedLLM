# test/test_timer.py
import asyncio
import sys
import os
import time

# Add app to path
# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from logic.timer_ops import tool_timer_add, tool_timer_list, tool_timer_delete
from logic.timer_storage import storage
from settings import GlobalResources

# Mock Redis
class MockRedis:
    def __init__(self):
        self.data = {}
    
    def set(self, key, value):
        self.data[key] = value
        return True
    
    def get(self, key):
        return self.data.get(key)
    
    def keys(self, pattern):
        # Simple pattern match
        prefix = pattern.replace('*', '')
        return [k for k in self.data.keys() if k.startswith(prefix)]
    
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            return 1
        return 0

async def test_timer_flow():
    print("--- Starting Timer/Alarm Flow Test ---")
    
    # Setup Mock Redis
    mock_redis = MockRedis()
    GlobalResources.redis_client = mock_redis
    
    user_creds = {"user": "test_user"}
    
    # 1. Test Timer Creation (Duration)
    print("\n1. Testing Timer Creation (10 minutes)...")
    res = await tool_timer_add("Set a timer for 10 minutes", user_creds, "test_model", mock_redis)
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "timer" in res["message"].lower()
    
    # 2. Test Alarm Creation (Absolute Time)
    print("\n2. Testing Alarm Creation (Wake me up at 8am)...")
    res = await tool_timer_add("Wake me up at 8am", user_creds, "test_model", mock_redis)
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "alarm" in res["message"].lower()
    
    # 3. Test List
    print("\n3. Testing List...")
    res = await tool_timer_list(user_creds, mock_redis)
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    assert "Active Timers" in res["message"]
    assert "10 minutes" in res["message"] or "Timer" in res["message"]
    
    # 4. Test Delete
    print("\n4. Testing Delete...")
    res = await tool_timer_delete("delete timer", user_creds, mock_redis)
    print(f"Result: {res}")
    assert res["status"] == "SUCCESS"
    
    # Verify deletion
    res = await tool_timer_list(user_creds, mock_redis)
    # Should still have one left (the alarm)
    assert "Active Timers" in res["message"]
    
    print("\n--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_timer_flow())
