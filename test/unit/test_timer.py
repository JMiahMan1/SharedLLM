# test/test_timer.py
import asyncio
import sys
import os
import time

# Mocking dependencies for test environment
from unittest.mock import MagicMock
sys.modules['fastapi'] = MagicMock()
sys.modules['fastapi.middleware.cors'] = MagicMock()
sys.modules['fastapi.responses'] = MagicMock()
sys.modules['pydantic'] = MagicMock()
sys.modules['uvicorn'] = MagicMock()
from unittest.mock import patch
from datetime import datetime, timedelta

def mock_parse(date_string, settings=None):
    now = datetime.now()
    if '8am' in date_string:
        return now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return now + timedelta(days=1)

# Add app to path
# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from app.logic.timer_ops import tool_timer_add, tool_timer_list, tool_timer_delete, tool_alarm_add
from app.logic.timer_storage import storage
from app.settings import GlobalResources

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
    print("\n--- Starting Timer/Alarm Flow Test ---")

    # Setup Mock Redis
    mock_redis = MockRedis()
    GlobalResources.redis_client = mock_redis

    with patch('app.logic.timer_ops.dateparser.parse', side_effect=mock_parse):
        # 1. Testing Timer Creation (Duration)
        print("\n1. Testing Timer Creation (10 minutes)...")
        res = await tool_timer_add("set a timer for 10 minutes", {"user": "test"}, "mock-model", mock_redis)
        print(f"Result: {res}")
        assert res["status"] == "SUCCESS"
        assert res["service"] == "timer_add"
        assert "timer" in res["message"].lower()

        # 2. Testing Timer Creation with Absolute Time (Should Forward to Alarm)
        print("\n2. Testing Timer Creation with Absolute Time (Should Forward to Alarm)...")
        res = await tool_timer_add("set a timer for 8am", {"user": "test"}, "mock-model", mock_redis)
        print(f"Result: {res}")
        assert res["status"] == "SUCCESS"
        assert "alarm" in res["message"].lower()

        # 3. Testing Alarm Creation (Absolute Time)
        print("\n3. Testing Alarm Creation (Wake me up at 8am)...")
        res = await tool_alarm_add("wake me up at 8am", {"user": "test"}, "mock-model", mock_redis)
        print(f"Result: {res}")
        assert res["status"] == "SUCCESS"
        assert res["service"] == "timer_add" # Service name remains timer_add for frontend compatibility? No, let's check what I returned.
        # Actually I returned "timer_add" as service in _create_timer_entry. That's fine for now.
        assert "alarm" in res["message"].lower()

        # 4. Testing Alarm Creation with Recurrence
        print("\n4. Testing Alarm Creation with Recurrence (Every Day)...")
        res = await tool_alarm_add("set an alarm for 7am every day", {"user": "test"}, "mock-model", mock_redis)
        print(f"Result: {res}")
        assert res["status"] == "SUCCESS"
        assert "repeats daily" in res["message"].lower() or "repeats every day" in res["message"].lower()

        # 5. Testing List
        print("\n5. Testing List...")
        res = await tool_timer_list({"user": "test"}, mock_redis)
        print(f"Result: {res}")
        assert "Active Timers" in res["message"]
        assert "Active Alarms" in res["message"]

        # 6. Testing Delete
        print("\n6. Testing Delete...")
        res = await tool_timer_delete("delete timer", {"user": "test"}, mock_redis)
        print(f"Result: {res}")
        assert res["status"] == "SUCCESS"

    print("\n--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_timer_flow())
