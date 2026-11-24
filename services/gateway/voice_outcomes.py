"""How each person's requests to Jarvis went: the admin page's request outcomes
(docs/PROFILE_STATS.md).

For every assistant request (a voice satellite's or typed chat) the gateway
counts, per person and day: the outcome (``ok``, ``timeout``, ``error``,
``tool_failure``, or ``offline``: it fell back to the "the AI is offline"
answer), whether it waited in line
before it started (``queued``), how long it took (latency buckets), which
tools failed, and the hour of day. Counts and timings only: never the
question, the answer or a tool's arguments.

Writes are off the request's path: ``finish`` only adds to an in-memory
counter, and a background task (``flush_loop``) writes the counters to Redis
every ``VOICE_OUTCOME_FLUSH_S`` seconds as one pipeline of ``HINCRBY``. Each
day is one hash, ``outcomes:{user}:{day}``, kept ``VOICE_OUTCOME_RETENTION_DAYS``
(90) days, so the record is bounded.

How a request is followed:

* ``chat_handler`` calls ``begin`` (a record in a context variable); the
  handler names the person with ``set_user``; ``note_outcome`` marks the
  offline answer; ``_execute_single_tool`` calls ``note_tool``.
* A request answered in the handler is finished there (``finish_response``;
  a stream when it ends).
* A request handed to the job queue carries its record in the job payload
  (``defer``); the worker resumes it (``resume``) so its tool calls count, and
  ``JobQueue.complete_job``/``fail_job`` finish it (``finish_job``), with the
  time it waited before a worker took it.

Settings are read once at startup.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

log = logging.getLogger("gateway.voice_outcomes")

RETENTION_DAYS = int(os.getenv("VOICE_OUTCOME_RETENTION_DAYS", "90"))
FLUSH_S = float(os.getenv("VOICE_OUTCOME_FLUSH_S", "10"))
SLOW_MS = int(os.getenv("VOICE_OUTCOME_SLOW_MS", "8000"))
QUEUED_MS = int(os.getenv("VOICE_OUTCOME_QUEUED_MS", "3000"))

OUTCOMES = ("ok", "timeout", "error", "tool_failure", "offline")
FAILED = frozenset({"timeout", "error", "tool_failure", "offline"})
#: Upper bounds of the latency buckets, in milliseconds; the last bucket is open.
BUCKETS_MS = (1000, 2000, 4000, 8000, 15000, 30000, 60000)
KEY = "outcomes:{user}:{day}"
_TOOL = re.compile(r"[^a-z0-9_.-]+")

#: Phrases a single-turn tool answers with when it did not do the thing.
TOOL_FAILURE_MARKERS = (
    "sorry, i couldn't complete that action",
    "sorry, i encountered an error",
    "i don't have a handler for",
    "error:",
)


def bucket(ms: float) -> str:
    for upper in BUCKETS_MS:
        if ms < upper:
            return f"<{upper}"
    return f">={BUCKETS_MS[-1]}"


@dataclass
class Record:
    source: str
    t0: float = field(default_factory=time.time)
    user: str = ""
    tools: list[tuple[str, bool]] = field(default_factory=list)
    outcome: str | None = None
    deferred: bool = False
    done: bool = False


_current: contextvars.ContextVar[Record | None] = contextvars.ContextVar("voice_outcome", default=None)
_pending: Counter[tuple[str, str, str]] = Counter()
#: The job worker finishes requests on its own event loop in another thread, so
#: the counter is shared under a lock and each loop has its own Redis client.
_lock = threading.Lock()
_flusher: asyncio.Task | None = None


# --------------------------------------------------------------------------- following a request


def begin(source: str) -> Record:
    record = Record(source=source)
    _current.set(record)
    return record


def current() -> Record | None:
    return _current.get()


def set_user(user: str | None) -> None:
    record = _current.get()
    if record is not None and user:
        record.user = str(user).strip().lower()


def note_outcome(outcome: str) -> None:
    """An answer that is not the model's (``offline``)."""
    record = _current.get()
    if record is not None and outcome in OUTCOMES:
        record.outcome = outcome


def tool_failed(result: Any) -> bool:
    """Whether a single-turn tool's answer says it did not do the thing. An empty
    answer is not counted as a failure: some tools have nothing to say."""
    text = str(result or "").strip().lower()
    return any(text.startswith(m) for m in TOOL_FAILURE_MARKERS)


def note_tool(name: str, ok: bool) -> None:
    record = _current.get()
    if record is not None:
        record.tools.append((_TOOL.sub("_", str(name or "tool").lower())[:60], bool(ok)))


def defer() -> dict | None:
    """The record handed to the job queue (stored in the job payload)."""
    record = _current.get()
    if record is None or not record.user:
        return None
    record.deferred = True
    return {"source": record.source, "t0": record.t0, "tools": [list(t) for t in record.tools], "outcome": record.outcome}


def resume(payload: dict | None, user: str) -> Record | None:
    """In the worker: continue the record a deferred request carried."""
    info = (payload or {}).get("_outcome")
    if not isinstance(info, dict):
        return None
    record = Record(source=str(info.get("source") or "chat"), t0=float(info.get("t0") or time.time()), user=user,
                    tools=[(str(t[0]), bool(t[1])) for t in info.get("tools") or [] if isinstance(t, list | tuple) and len(t) == 2],
                    outcome=info.get("outcome"))
    _current.set(record)
    return record


# --------------------------------------------------------------------------- finishing


def finish(record: Record | None, outcome: str | None = None, *, queued: bool = False, ended: float | None = None) -> None:
    """Count one finished request. Only adds to memory: never waits on I/O."""
    if record is None or record.done or not record.user:
        return
    record.done = True
    final = outcome or record.outcome
    if final is None:
        final = "tool_failure" if any(not ok for _, ok in record.tools) else "ok"
    if final not in OUTCOMES:
        final = "error"
    ms = max(0.0, ((ended or time.time()) - record.t0) * 1000)
    when = datetime.fromtimestamp(ended or time.time())
    day = when.date().isoformat()
    hour = f"{when.hour:02d}"
    counts: Counter[tuple[str, str, str]] = Counter({
        (record.user, day, "n"): 1,
        (record.user, day, f"o:{final}"): 1,
        (record.user, day, f"s:{record.source}"): 1,
        (record.user, day, f"l:{bucket(ms)}"): 1,
        (record.user, day, "ms"): int(ms),
        (record.user, day, f"h:{hour}:n"): 1,
    })
    if ms >= SLOW_MS:
        counts[(record.user, day, f"h:{hour}:slow")] += 1
    if queued:
        counts[(record.user, day, "q")] += 1
    for name, ok in record.tools:
        counts[(record.user, day, f"t:{name}:calls")] += 1
        if not ok:
            counts[(record.user, day, f"t:{name}:fail")] += 1
    with _lock:
        _pending.update(counts)
    _ensure_flusher()


def finish_response(record: Record | None, response: Any) -> Any:
    """Finish a request the handler answered itself. A stream is finished when it
    ends; a request handed to the job queue is finished by the job."""
    if record is None or record.deferred:
        return response
    status = int(getattr(response, "status_code", 200) or 200)
    if status in (400, 401, 403, 404, 422):
        record.done = True  # not a request Jarvis tried to answer
        return response
    if status >= 500 and record.outcome is None:
        record.outcome = "timeout" if status == 504 else "error"
    body = getattr(response, "body_iterator", None)
    if body is None:
        finish(record)
        return response

    async def counted():
        failed = False
        try:
            async for chunk in body:
                yield chunk
        except BaseException:
            failed = True
            raise
        finally:
            finish(record, "error" if failed and record.outcome is None else None)

    response.body_iterator = counted()
    return response


def finish_job(job: dict, *, failed: bool, error: str = "", detail: dict | None = None) -> None:
    """Finish a request that went through the job queue (messaging.JobQueue)."""
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    info = payload.get("_outcome")
    if not isinstance(info, dict):
        return
    running = _current.get()
    record = running if running is not None and not running.done and running.t0 == float(info.get("t0") or 0) else None
    if record is None:
        record = Record(source=str(info.get("source") or "chat"), t0=float(info.get("t0") or time.time()),
                        user=str(job.get("user_id") or "").strip().lower(), outcome=info.get("outcome"),
                        tools=[(str(t[0]), bool(t[1])) for t in info.get("tools") or [] if isinstance(t, list | tuple) and len(t) == 2])
    outcome = None
    if failed:
        code = f"{(detail or {}).get('code') or ''} {error}".lower()
        if (detail or {}).get("offline"):
            outcome = "offline"
        elif "timeout" in code or "timed out" in code:
            outcome = "timeout"
        else:
            outcome = "error"
    created, started = job.get("created_at"), job.get("started_at")
    queued = bool(created and started and (float(started) - float(created)) * 1000 >= QUEUED_MS)
    finish(record, outcome, queued=queued, ended=job.get("completed_at"))


# --------------------------------------------------------------------------- storage


def _redis():
    import redis.asyncio as redis

    from services.gateway.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


_client: Any = None
_clients: dict[int, Any] = {}


def client():
    """A Redis client for the running event loop (a client is bound to its loop)."""
    if _client is not None:  # tests set one
        return _client
    try:
        key = id(asyncio.get_running_loop())
    except RuntimeError:
        key = 0
    if key not in _clients:
        _clients[key] = _redis()
    return _clients[key]


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and not _flusher.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flusher = loop.create_task(flush_loop())


async def flush() -> int:
    """Write what is counted so far; returns how many counters were written."""
    with _lock:
        if not _pending:
            return 0
        batch = dict(_pending)
        _pending.clear()
    try:
        pipe = client().pipeline(transaction=False)
        keys = set()
        for (user, day, name), n in batch.items():
            key = KEY.format(user=user, day=day)
            pipe.hincrby(key, name, int(n))
            keys.add(key)
        for key in keys:
            pipe.expire(key, RETENTION_DAYS * 86400)
        await pipe.execute()
        return len(batch)
    except Exception as exc:  # counting must never cost a request; keep them for the next try
        log.warning("[voice-outcomes] could not write %d counters: %s", len(batch), exc)
        with _lock:
            _pending.update(batch)
        return 0


async def flush_loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_S)
        with contextlib.suppress(Exception):
            await flush()
        if not _pending:
            # Sleep until something is counted again.
            return


# --------------------------------------------------------------------------- reading


def _percentile(buckets: dict[str, int], share: float) -> int | None:
    total = sum(buckets.values())
    if not total:
        return None
    seen = 0
    for upper in BUCKETS_MS:
        seen += buckets.get(f"<{upper}", 0)
        if seen >= total * share:
            return upper
    return BUCKETS_MS[-1]


async def summary(user: str, days: int = 30, today: date | None = None) -> dict | None:
    """The admin page's request outcomes for one person; None when nothing is recorded."""
    with contextlib.suppress(Exception):
        await flush()
    today = today or date.today()
    days = max(1, min(int(days), RETENTION_DAYS))
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    pipe = client().pipeline(transaction=False)
    for day in day_list:
        pipe.hgetall(KEY.format(user=user.strip().lower(), day=day))
    rows = await pipe.execute()
    outcomes: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    tools_calls: Counter[str] = Counter()
    tools_fail: Counter[str] = Counter()
    hours_n: Counter[int] = Counter()
    hours_slow: Counter[int] = Counter()
    sources: Counter[str] = Counter()
    per_day = []
    queued = 0
    for day, row in zip(day_list, rows, strict=True):
        row = {k: int(v) for k, v in (row or {}).items()}
        if not row:
            continue
        failed = 0
        for k, v in row.items():
            if k.startswith("o:"):
                outcomes[k[2:]] += v
                if k[2:] in FAILED:
                    failed += v
            elif k.startswith("l:"):
                buckets[k[2:]] += v
            elif k.startswith("s:"):
                sources[k[2:]] += v
            elif k.startswith("t:"):
                name, _, kind = k[2:].rpartition(":")
                (tools_fail if kind == "fail" else tools_calls)[name] += v
            elif k.startswith("h:"):
                _, hour, kind = k.split(":")
                (hours_slow if kind == "slow" else hours_n)[int(hour)] += v
            elif k == "q":
                queued += v
        per_day.append({"day": day, "total": row.get("n", 0), "ok": row.get("o:ok", 0), "failed": failed})
    total = sum(d["total"] for d in per_day)
    if not total:
        return None
    if queued:
        outcomes["queued"] = queued
    return {
        "days": days,
        "total": total,
        "outcomes": dict(outcomes),
        "sources": dict(sources),
        "latency": {"buckets": dict(buckets), "p50_ms": _percentile(buckets, 0.5), "p90_ms": _percentile(buckets, 0.9),
                    "slow_ms": SLOW_MS},
        "per_day": per_day,
        "tools": sorted(
            ({"tool": t, "failures": tools_fail[t], "calls": tools_calls[t]} for t in set(tools_fail) | set(tools_calls) if tools_fail[t]),
            key=lambda t: (-t["failures"], t["tool"]),
        ),
        "slow_hours": sorted(
            ({"hour": h, "slow": hours_slow[h], "total": hours_n[h]} for h in hours_n if hours_slow[h]),
            key=lambda h: (-(h["slow"] / max(1, h["total"])), -h["slow"]),
        ),
    }
