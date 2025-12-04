# app/logic/alarm_ops.py
import asyncio
import time
import dateparser
from datetime import datetime, timedelta
from typing import Dict, Union
from settings import log, GlobalResources

async def _alarm_countdown(alarm_id: str, trigger_time: datetime, message: str):
    """Background task that waits until trigger_time and then 'rings'."""
    try:
        delay = (trigger_time - datetime.now()).total_seconds()
        if delay > 0:
            log.info(f"Alarm {alarm_id} set for {trigger_time} (in {delay:.1f}s)")
            await asyncio.sleep(delay)
        
        # Ring!
        log.info(f"ALARM RINGING: {message}")
        # In a real app, this would push a notification or play a sound via HA.
        # For now, we just log it.
        
    except asyncio.CancelledError:
        log.info(f"Alarm {alarm_id} cancelled.")
    except Exception as e:
        log.error(f"Alarm {alarm_id} failed: {e}")
    finally:
        # Cleanup
        if alarm_id in GlobalResources.alarms:
            del GlobalResources.alarms[alarm_id]

async def tool_alarm_set(query: str) -> Dict[str, Union[str, bool]]:
    # Extract time
    now = datetime.now()
    # Use dateparser to extract time from query like "Set an alarm for 5 minutes" or "Wake me up at 8am"
    dt = dateparser.parse(query, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now})
    
    if not dt:
        return {"status": "FAILURE", "message": "Could not understand the time.", "service": "alarm_set"}
        
    # If parsed time is in the past, assume tomorrow (for times like "8am" when it's 9am)
    if dt < now:
         dt += timedelta(days=1)

    alarm_id = f"alarm_{int(time.time())}_{len(GlobalResources.alarms)}"
    message = f"Alarm for {query}"
    
    task = asyncio.create_task(_alarm_countdown(alarm_id, dt, message))
    GlobalResources.alarms[alarm_id] = {"task": task, "time": dt, "message": message}
    
    return {"status": "SUCCESS", "message": f"Alarm set for {dt.strftime('%I:%M %p')}.", "service": "alarm_set"}

async def tool_alarm_list() -> Dict[str, Union[str, bool]]:
    if not GlobalResources.alarms:
        return {"status": "SUCCESS", "message": "No active alarms.", "service": "alarm_list"}
        
    lines = []
    for aid, data in GlobalResources.alarms.items():
        t_str = data["time"].strftime('%I:%M %p')
        lines.append(f"- {t_str}")
        
    return {"status": "SUCCESS", "message": "Active Alarms:\n" + "\n".join(lines), "service": "alarm_list"}

async def tool_alarm_delete(query: str) -> Dict[str, Union[str, bool]]:
    if "all" in query.lower():
        count = 0
        for aid in list(GlobalResources.alarms.keys()):
            GlobalResources.alarms[aid]["task"].cancel()
            # Cleanup happens in finally block of task, but we can force it here too to be safe/fast
            if aid in GlobalResources.alarms:
                del GlobalResources.alarms[aid]
            count += 1
        return {"status": "SUCCESS", "message": f"Cancelled {count} alarms.", "service": "alarm_delete"}

    # Find by time fuzzy match
    dt = dateparser.parse(query, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()})
    
    deleted = []
    for aid, data in list(GlobalResources.alarms.items()):
        # Match if time is within 60 seconds
        if dt and abs((data["time"] - dt).total_seconds()) < 60:
            data["task"].cancel()
            if aid in GlobalResources.alarms:
                del GlobalResources.alarms[aid]
            deleted.append(aid)
            
    if deleted:
        return {"status": "SUCCESS", "message": f"Cancelled {len(deleted)} alarm(s).", "service": "alarm_delete"}
        
    return {"status": "FAILURE", "message": "No matching alarm found.", "service": "alarm_delete"}
