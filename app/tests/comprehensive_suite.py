
import asyncio
import os
import sys
import json
import time
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("ComprehensiveSuite")

# Add app directory to path
# Add project root to path (one level up from app/)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.logic.execution.registry import ActionDispatcher
from app.logic.execution.handlers import *
from app.logic.media_ops import handle_media_command
from app.logic.timer_ops import tool_timer_add, tool_timer_list, tool_timer_delete, tool_alarm_add, tool_timer_pause, tool_timer_resume
from app.logic.calendar_ops import tool_calendar_list, tool_calendar_add, tool_calendar_delete
from app.logic.note_ops import tool_note_add, tool_note_delete, tool_note_read, tool_note_append
from app.logic.web_search import tool_web_search
from app.settings import HA_URL, HA_ENV_TOKEN, REDIS_HOST, REDIS_PORT, REDIS_DB

# Alias for compatibility
HA_TOKEN = HA_ENV_TOKEN

class GlobalResources:
    redis_client = None
    ha_collection = None

async def load_resources():
    import redis.asyncio as redis
    from app.logic.db.ha_collection import HACollection
    
    log.info("Loading Global Resources...")
    GlobalResources.redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    GlobalResources.ha_collection = HACollection()
    log.info("Resources Loaded.")

async def get_user_creds():
    return {
        "user_id": "jeremiah",
        "username": "jeremiah",
        "home_assistant_url": HA_URL,
        "home_assistant_token": HA_TOKEN
    }

class TestFailure(Exception):
    pass

class TestReport:
    def __init__(self):
        self.results = []
        self.start_time = time.time()

    def add_result(self, feature, trigger, status, duration, error=None):
        self.results.append({
            "feature": feature,
            "trigger": trigger,
            "status": status,
            "duration_seconds": round(duration, 4),
            "error": str(error) if error else None,
            "timestamp": datetime.now().isoformat()
        })
    
    def save(self, path="temp/test_report.json"):
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2)
        log.info(f"Report saved to {path}")

async def run_feature(report, feature_name, trigger, func, *args):
    start = time.time()
    log.info(f"--- TEST: {feature_name} ---")
    log.info(f"Trigger: '{trigger}'")
    try:
        res = await func(*args)
        duration = time.time() - start
        
        # Validation Logic
        success = False
        if isinstance(res, dict):
            if res.get("status") == "SUCCESS":
                success = True
            elif res.get("status") is None: 
                 success = True 
        elif isinstance(res, list):
             if len(res) > 0:
                 success = True
        
        if success:
            log.info(f"PASS ({duration:.2f}s)")
            report.add_result(feature_name, trigger, "PASS", duration)
            return res
        else:
            raise TestFailure(f"Response indicated failure: {res}")

    except Exception as e:
        duration = time.time() - start
        log.error(f"FAIL ({duration:.2f}s): {e}")
        report.add_result(feature_name, trigger, "FAIL", duration, e)
        raise e

async def run_suite():
    await load_resources()
    user_creds = await get_user_creds()
    report = TestReport()
    entity = "media_player.office_tv" 

    try:
        # 1. Power Logic
        await run_feature(report, "Power Off", "turn off office tv", 
                          handle_media_command, "turn_off", "turn off office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        await asyncio.sleep(2)
        await run_feature(report, "Power On", "turn on office tv", 
                          handle_media_command, "turn_on", "turn on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        await asyncio.sleep(5)

        # 2. Media Controls
        await run_feature(report, "Volume Set", "set volume to 15% on office tv",
                          handle_media_command, "volume_set", "set volume to 15% on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        
        await run_feature(report, "Volume Up", "volume up on office tv",
                          handle_media_command, "volume_up", "volume up on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        
        await run_feature(report, "Volume Down", "volume down on office tv",
                          handle_media_command, "volume_down", "volume down on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        
        await run_feature(report, "Mute", "mute office tv",
                          handle_media_command, "volume_mute", "mute office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        await asyncio.sleep(1)

        await run_feature(report, "Unmute", "unmute office tv",
                          handle_media_command, "volume_mute", "unmute office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)

        # 3. Watch / Search (The requested addition)
        await run_feature(report, "Web Search", "search for python language",
                          tool_web_search, "python language")
                          
        # "Watch" test - This is the Critical Path for new feedback
        # We expect this to default to app launch currently. 
        # Future success criteria: Returns result indicating deep link or specific search sent to TV.
        log.info("--- TEST: Watch (Deep Link) ---")
        await run_feature(report, "Watch Content", "watch Brandon Lake videos on office tv",
                          handle_media_command, "play_media", "watch Brandon Lake videos on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)


        # 4. Apps
        await run_feature(report, "App Launch", "launch youtube on office tv",
                          handle_media_command, "open_app", "launch youtube on office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)


        # 5. Timers & Alarms
        await run_feature(report, "Timer Add", "set timer for 1 min on office tv",
                          tool_timer_add, "set timer for 1 min on office tv", user_creds, "test", GlobalResources.redis_client, GlobalResources.ha_collection)
        
        await run_feature(report, "Timer Pause", "pause timer on office tv",
                          tool_timer_pause, "pause timer on office tv")
                          
        await run_feature(report, "Timer Resume", "resume timer on office tv",
                          tool_timer_resume, "resume timer on office tv")

        await run_feature(report, "Timer List", "show timers",
                          tool_timer_list, user_creds, GlobalResources.redis_client)
                          
        await run_feature(report, "Timer Delete", "cancel timer",
                          tool_timer_delete, "timer", user_creds, GlobalResources.redis_client)

        await run_feature(report, "Alarm Add", "set alarm for 8am on office tv",
                          tool_alarm_add, "set alarm for 8am on office tv", user_creds, "test", GlobalResources.redis_client, GlobalResources.ha_collection)
        
        await run_feature(report, "Alarm Delete", "cancel alarm",
                          tool_timer_delete, "alarm", user_creds, GlobalResources.redis_client)

        # 6. Calendar
        await run_feature(report, "Calendar Add", "add event Test Event tomorrow at 2pm",
                          tool_calendar_add, "add event Test Event tomorrow at 2pm", user_creds, "test", GlobalResources.redis_client)
        
        await run_feature(report, "Calendar List", "list events",
                          tool_calendar_list, user_creds, GlobalResources.redis_client)
        
        await run_feature(report, "Calendar Delete", "delete event Test Event",
                          tool_calendar_delete, "delete event Test Event", user_creds, "test", GlobalResources.redis_client)

        # 7. Notes
        await run_feature(report, "Note Add", "create note Test Note content Hello World",
                          tool_note_add, "Test Note", "Hello World", "General")
        
        await run_feature(report, "Note Read", "read note Test Note",
                          tool_note_read, "Test Note")

        await run_feature(report, "Note Append", "add to note Test Note content Goodbye",
                          tool_note_append, "Test Note", "Goodbye")
        
        await run_feature(report, "Note Delete", "delete note Test Note",
                          tool_note_delete, "Test Note")
        
        # 8. Teardown
        await run_feature(report, "Teardown (Power Off)", "turn off office tv", 
                          handle_media_command, "turn_off", "turn off office tv", entity, user_creds, GlobalResources.ha_collection, GlobalResources.redis_client)

    except Exception as e:
        log.error("SUITE ABORTED")
        raise e
    finally:
        report.save()
        if GlobalResources.redis_client:
            await GlobalResources.redis_client.close()

if __name__ == "__main__":
    asyncio.run(run_suite())
