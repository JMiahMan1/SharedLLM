# Priority-Based Preemption System for Raven Missions

## Overview

This document describes the priority-based preemption system implemented for the Jarvis/SharedLLM agentic loop. The system allows high-priority tasks (voice commands, home automation, emergencies) to preempt long-running Raven missions (coding, building) that would otherwise block the inference queue for hours.

## Problem Statement

Before this system:
- Raven missions (coding, building games, etc.) hold the exclusive TIER3_LOCK for hours
- Voice commands, home automation, and quick queries queue behind them
- No way to express urgency or priority in the inference system
- Family members experience delays for time-sensitive requests

## Solution Architecture

### 1. Priority Levels

| Priority | Value | Use Case |
|----------|-------|----------|
| LOW | 0 | Raven missions (coding, building, long-running) |
| NORMAL | 50 | Standard chat, librarian queries, questions |
| HIGH | 100 | Voice commands, home automation, quick control |
| CRITICAL | 200 | Emergency (SOS, security alerts, urgent) |

### 2. Priority Inference (Automatic)

Jobs don't need explicit priority - it's inferred from payload content:

```python
# High priority indicators (auto-detected):
- "turn on/off", "toggle", "set temperature"
- "lock/unlock", "open/close garage"
- "play/stop/volume/brightness"
- "what is the weather", "what time"
- Any query with `voice_context: true`

# Critical priority indicators:
- "sos", "emergency", "help me", "urgent"

# Low priority (Raven missions):
- Any job with `_mission_id` in payload

# Normal priority (default):
- Standard chat, questions, librarian queries
```

### 3. Tier Inference

Jobs are automatically routed to the correct tier:

| Tier | Concurrency | Job Types |
|------|-------------|-----------|
| TIER2 (Librarian) | 3 concurrent | Voice, home automation, chat, questions, librarian |
| TIER3 (Raven) | 1 exclusive | Missions, coding tasks, building, scraping |

Auto-detection for coding tasks (non-mission):
```python
coding_keywords = ["raven", "build a", "create a", "fix the", "write code", 
                   "implement", "write a python", "write a script", "scrape",
                   "crawler", "api wrapper", "backend", "frontend"]
```

### 4. Priority Queue (Redis Sorted Set)

Replaces FIFO list with sorted set for priority ordering:
- Score = `-priority * 1_000_000_000 + created_at`
- Lower score = higher priority (earlier execution)
- Maintains FIFO within same priority

### 5. Preemption Mechanism

When a high-priority job arrives while a Raven mission is running:

```
1. High-priority job enqueued with HIGH/CRITICAL priority
2. Worker claims job, checks for running Raven mission
3. If Raven mission running AND priority > NORMAL:
   a. Set Redis key: `raven:mission:pause:{mission_id}` = "PREEMPTED"
   b. Publish "PREEMPTED" on same channel
4. Raven mission's agent loop receives pause signal
5. Mission saves checkpoint, yields TIER3_LOCK
6. High-priority job runs immediately
7. After completion, Raven mission resumes from checkpoint
```

### 6. Key Components

#### `services/gateway/messaging.py`
- `InferenceJobQueue`: Priority-aware queue with preemption support
- `infer_priority_from_payload()`: Auto-detect priority from payload
- `infer_tier_from_payload()`: Auto-detect tier from payload
- `request_preemption()`: Pause running Raven mission
- `register_raven_mission()` / `unregister_raven_mission()`: Track running missions

#### `services/gateway/background_worker.py`
- Registers missions when acquiring TIER3_LOCK
- Unregisters on completion/failure/timeout
- Worker claims highest-priority job from sorted set

#### `services/gateway/agent_loop.py` (existing)
- Already had `_await_mission_resume()` for pause/resume
- Listens on `raven:mission:pause:{mission_id}` channel
- Handles "PREEMPTED" signal same as manual pause

## API Usage

### Dispatching a High-Priority Job

```python
# Voice command automatically gets HIGH priority
payload = {
    "query": "turn off the lights",
    "voice_context": True  # Auto-detected as HIGH priority, tier2
}
await job_queue.enqueue_job(user_id="default", payload=payload)

# Emergency explicitly
payload = {
    "query": "emergency shutdown",
    "priority": 200  # CRITICAL
}
await job_queue.enqueue_job(user_id="default", payload=payload)

# Raven mission (auto LOW priority, tier3)
payload = {
    "query": "Build a space shooter",
    "_mission_id": 42  # Auto LOW priority, tier3
}
await job_queue.enqueue_job(user_id="default", payload=payload)
```

### Manual Pause/Resume (Existing)

```bash
# Pause a mission
curl -X POST http://ai.local:8080/api/raven/missions/5/pause \
  -H "Authorization: Bearer <api_key>"

# Resume
curl -X POST http://ai.local:8080/api/raven/missions/5/resume \
  -H "Authorization: Bearer <api_key>"
```

## Configuration

### Environment Variables
None required - priority inference works automatically.

### Redis Keys

| Key Pattern | Purpose |
|-------------|---------|
| `raven:inference_queue` | Sorted set (priority queue) |
| `raven:inference_processing` | List of processing jobs |
| `raven:job:{job_id}` | Job metadata |
| `raven:mission:pause:{mission_id}` | Pause/preemption signal |
| `raven:mission:kill:{mission_id}` | Kill signal |

### Timeouts
- Pause key TTL: 1 hour (3600 seconds)
- Job TTL: 1 hour (3600 seconds)
- Lease TTL: 120 seconds

## Monitoring

### Check Queue Status
```bash
# View queued jobs (sorted by priority)
redis-cli ZRANGE raven:inference_queue 0 -1 WITHSCORES

# View processing jobs
redis-cli LRANGE raven:inference_processing 0 -1

# Check running Raven mission
redis-cli GET raven:preemption  # Not used currently
```

### Logs to Watch
```
[Preemption] Mission 42 preempted by job abc123 (priority HIGH)
[Worker] Mission 42 paused, waiting for resume signal.
[Worker] Mission 42 resumed.
```

## Testing

### Unit Tests
```bash
# Test priority/tier inference
python -c "
from services.gateway.messaging import infer_priority_from_payload, infer_tier_from_payload
tests = [
    ({'query': 'turn on lights', 'voice_context': True}, 100, 'tier2'),
    ({'query': 'write python scraper'}, 50, 'tier3'),
    ({'_mission_id': 1}, 0, 'tier3'),
]
for p, exp_p, exp_t in tests:
    assert infer_priority_from_payload(p) == exp_p
    assert infer_tier_from_payload(p) == exp_t
print('All tests passed!')
"
```

### Integration Test
```bash
# Requires live server
LIVE_E2E=1 GH_TOKEN=xxx pytest tests/integration/test_integration_raven_user_cases.py --run-local -s
```

## Known Limitations

1. **No preemption of tier2 jobs**: Only Raven (tier3) missions can be preempted. Tier2 jobs run to completion (typically <30s).

2. **Single Raven mission at a time**: TIER3_LOCK ensures only one Raven mission runs. Preemption pauses the current one.

3. **Priority inversion possible**: If a NORMAL job starts, then HIGH job arrives, HIGH waits. Could add priority donation in future.

4. **No persistence across restarts**: If gateway restarts, paused mission state is in Redis (survives) but worker needs to re-register.

5. **Race condition window**: Small window between claiming job and registering mission. Mitigated by checking before execution.

## Future Improvements

1. **Priority donation**: Boost priority of job blocking high-priority work
2. **Tier2 preemption**: Allow critical jobs to interrupt tier2 (with care)
3. **Deadline-aware scheduling**: Jobs with deadlines get priority boost
4. **Metrics/observability**: Prometheus metrics for queue depth, preemption rate
5. **Admin API**: Manual priority override for specific jobs

## Related Files

| File | Purpose |
|------|---------|
| `services/gateway/messaging.py` | Priority queue, preemption, inference |
| `services/gateway/background_worker.py` | Worker integration, mission registration |
| `services/gateway/agent_loop.py` | Pause/resume handling (existing) |
| `services/gateway/main.py` | Pause/resume HTTP endpoints (existing) |
| `docs/PRIORITY_PREEMPTION.md` | This documentation |
