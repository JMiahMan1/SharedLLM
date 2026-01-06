
# app/logic/timer_scheduler.py
import asyncio
import time
from datetime import datetime
from app.settings import log, GlobalResources
from app.logic.timer_storage import storage
from app.logic.timer_ops import trigger_alarm

SCHEDULER_INTERVAL = 5 # seconds

_running = False

async def scheduler_loop():
    global _running
    _running = True
    log.info("Timer Scheduler Started.")
    
    while _running:
        try:
            timers = await storage.list_timers()
            now = datetime.now()
            
            for t in timers:
                if not t.get("active", True): continue
                
                expires = datetime.fromisoformat(t["expires_at"])
                
                # Check within tolerance
                if now >= expires:
                    # Fire!
                    # Run in background to not block scheduler
                    asyncio.create_task(trigger_alarm(t))
                    
        except Exception as e:
            log.error(f"Scheduler Error: {e}")
            
        await asyncio.sleep(SCHEDULER_INTERVAL)

async def start_scheduler():
    await scheduler_loop()

async def stop_scheduler():
    global _running
    _running = False
    log.info("Timer Scheduler Stopped.")
