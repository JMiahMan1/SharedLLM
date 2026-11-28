"""Reuse of tool and retrieval results within a conversation, by declared freshness.

The knowledge map moved information out of the prompt and behind tools, so a
follow-up ("and what about tomorrow?", "read me the next verse") calls the same
tool again. This keeps each result for as long as its kind of data stays true:

* **Freshness is declared, not guessed** (``tool_registry.FRESHNESS``): ``live``
  is never reused, ``short`` for minutes, ``daily`` until local midnight,
  ``static`` for days. An unknown tool or action is live.
* **Per conversation and per user.** A key is the user, the conversation, the
  tool and its normalised arguments. Only data the registry marks household
  public (the Bible's text) is shared between people.
* **Writes invalidate.** A write through a tool drops every cached read that
  shares one of its tags, and events (an index rebuild, a device sync) can drop
  a tag too (``invalidate_tag``).
* **Said, not hidden.** A reused result reaches the model with the time it was
  fetched, so the answer can say how current it is.
* **Learns.** Hits, misses, and whether a refetch found the same value are
  counted per tool and per arguments; ``tune()`` suggests a longer or shorter
  class and, within ``TOOL_CACHE_MAX_CLASS``, applies it. Never for a live tool.
* **Keeps hot household data warm.** ``refresh_hot()`` refetches frequently used,
  household-public entries before they expire. Per-user entries are not
  refreshed in the background: that would mean acting as a person who is not asking.

Redis is the store. When it cannot be reached the call goes to the source, as a
miss, and the failure is counted and logged: a cache must never be the reason a
question goes unanswered.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from services.gateway import tool_registry as tr

log = logging.getLogger("gateway.tool_cache")

PREFIX = "toolcache:v1"


def _bounded(key: str, default: float, lo: float, hi: float, cast=float):
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return cast(default)
    try:
        value = cast(float(raw))
    except ValueError:
        raise RuntimeError(f"{key}={raw!r} is not a number") from None
    if not lo <= value <= hi:
        raise RuntimeError(f"{key}={value} is outside {lo}..{hi}")
    return value


def _class_env(key: str, default: str) -> str:
    raw = (os.getenv(key) or default).strip().lower()
    if raw not in tr.FRESHNESS_CLASSES:
        raise RuntimeError(f"{key}={raw!r} is not one of {', '.join(tr.FRESHNESS_CLASSES)}")
    return raw


#: Read once at startup; a bad value stops the service with the variable named.
ENABLED = os.getenv("TOOL_CACHE_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
SHORT_S = _bounded("TOOL_CACHE_SHORT_S", 120, 5, 3600, int)
STATIC_S = _bounded("TOOL_CACHE_STATIC_S", 7 * 86400, 3600, 30 * 86400, int)
#: A conversation's entries expire this long after their last use (whatever their class).
CONVERSATION_IDLE_S = _bounded("TOOL_CACHE_CONVERSATION_IDLE_S", 1800, 60, 86400, int)
MAX_ENTRIES_PER_USER = _bounded("TOOL_CACHE_MAX_ENTRIES_PER_USER", 200, 10, 5000, int)
MAX_VALUE_CHARS = _bounded("TOOL_CACHE_MAX_VALUE_CHARS", 16000, 500, 200000, int)
#: The longest class tuning may promote a tool to.
MAX_CLASS = _class_env("TOOL_CACHE_MAX_CLASS", tr.DAILY)
AUTO_TUNE = os.getenv("TOOL_CACHE_AUTO_TUNE", "true").strip().lower() not in ("0", "false", "no", "off")
#: Evidence tuning needs before it changes a class.
TUNE_MIN_SAMPLES = _bounded("TOOL_CACHE_TUNE_MIN_SAMPLES", 20, 3, 100000, int)
TUNE_PROMOTE_SAME = _bounded("TOOL_CACHE_TUNE_PROMOTE_SAME", 0.95, 0.5, 1.0)
TUNE_DEMOTE_CHANGED = _bounded("TOOL_CACHE_TUNE_DEMOTE_CHANGED", 0.3, 0.05, 1.0)
#: Uses within a TTL that make a household-public entry "hot" (kept warm).
HOT_USES = _bounded("TOOL_CACHE_HOT_USES", 3, 1, 1000, int)
REFRESH_BEFORE = _bounded("TOOL_CACHE_REFRESH_BEFORE", 0.2, 0.05, 0.9)
REFRESH_MAX_PER_CYCLE = _bounded("TOOL_CACHE_REFRESH_MAX_PER_CYCLE", 10, 1, 500, int)
TUNING_LOG_MAX = 100

#: The conversation a tool call belongs to (set by the orchestrator for each turn).
CONVERSATION: contextvars.ContextVar[str] = contextvars.ContextVar("tool_cache_conversation", default="default")

_ERROR_PREFIXES = (
    "sorry, i couldn't", "sorry, i encountered", "i don't have a handler", "error", "nothing in ",
    "collection_name is required",
)

_client_override: Any = None

#: After a Redis failure the cache stands aside this long (every call goes to the source),
#: so an outage costs one timeout, not one per tool call. Device commands never touch it.
BACKOFF_S = 30.0
_down_until = 0.0
#: Tags a write could not invalidate while Redis was away, dropped as soon as it is back:
#: otherwise a lesson written during an outage could be served stale for days after it.
_pending_invalidations: set[str] = set()


class CacheUnavailable(RuntimeError):
    """Redis failed recently; the cache is standing aside until BACKOFF_S has passed."""


def set_client(client: Any) -> None:
    """Use ``client`` (a redis-py compatible object) instead of the gateway's Redis. Tests."""
    global _client_override, _down_until
    _client_override = client
    _down_until = 0.0
    _pending_invalidations.clear()


def _mark_down(exc: Exception) -> None:
    global _down_until
    if not isinstance(exc, CacheUnavailable):
        _down_until = time.monotonic() + BACKOFF_S
        log.warning("[tool-cache] Redis failed (%s: %s); calls go to the source for %ss",
                    type(exc).__name__, exc, int(BACKOFF_S))


def _redis():
    if time.monotonic() < _down_until:
        raise CacheUnavailable("the tool cache is standing aside after a Redis failure")
    if _client_override is not None:
        client = _client_override
    else:
        from services.gateway.cache import get_redis

        client = get_redis()
    if _pending_invalidations:
        pending = list(_pending_invalidations)
        _pending_invalidations.clear()
        for tag in pending:
            keys = list(client.smembers(f"{PREFIX}:tag:{tag}"))
            if keys:
                client.delete(*keys)
            client.delete(f"{PREFIX}:tag:{tag}")
            log.info("[tool-cache] dropped %s entries tagged %s after the outage", len(keys), tag)
    return client


def _now() -> float:
    return time.time()


def ttl_for(cls: str, now: float | None = None) -> int:
    """Seconds a value of ``cls`` stays good (0 for live)."""
    if cls == tr.SHORT:
        return SHORT_S
    if cls == tr.DAILY:
        t = datetime.fromtimestamp(now if now is not None else _now())
        midnight = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(60, int((midnight - t).total_seconds()))
    if cls == tr.STATIC:
        return STATIC_S
    return 0


def normalize_args(payload: dict | None) -> str:
    """The arguments that decide a result, as stable text (case, spacing and key order ignored)."""
    def norm(v: Any) -> Any:
        if isinstance(v, str):
            return " ".join(v.lower().split())
        if isinstance(v, dict):
            return {k: norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v

    clean = {k: v for k, v in (payload or {}).items() if k not in ("user_context", "tool", "name", "payload")}
    return json.dumps(norm(clean), sort_keys=True, default=str)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _user_key(user: str) -> str:
    return _digest((user or "anonymous").lower())


def effective_class(tool: str, declared: str) -> str:
    """The declared class with any applied tuning. A live declaration is never changed."""
    if declared == tr.LIVE:
        return tr.LIVE
    try:
        override = _redis().hget(f"{PREFIX}:overrides", tool)
    except Exception as exc:
        _mark_down(exc)
        return declared
    return override if override in tr.FRESHNESS_CLASSES else declared


def _entry_key(tool: str, args: str, *, user: str, conversation: str, public: bool) -> str:
    if public:
        return f"{PREFIX}:pub:{tool}:{_digest(args)}"
    return f"{PREFIX}:conv:{_user_key(user)}:{_digest(conversation)}:{tool}:{_digest(args)}"


# Millisecond fields are floats, and Redis keeps one hash value typed for good.
# Choosing the command by looking at the value meant a first `source_ms` of 972.0
# (a float whose `.is_integer()` is true) was written with HINCRBY, and the next
# one with a real fraction - 12.5 - was refused: "hash value is not an integer".
# That error marked the whole cache down, so every call went to its source for
# 30 s. The command has to follow the field, never the number in it.
FLOAT_STATS = frozenset({"source_ms", "saved_ms"})


def _stat(tool: str, **fields: float) -> None:
    try:
        pipe = _redis().pipeline()
        for name, amount in fields.items():
            if name in FLOAT_STATS or (isinstance(amount, float) and not amount.is_integer()):
                pipe.hincrbyfloat(f"{PREFIX}:stats:{tool}", name, amount)
            else:
                pipe.hincrby(f"{PREFIX}:stats:{tool}", name, int(amount))
        pipe.execute()
    except Exception as exc:
        _mark_down(exc)


def _looks_failed(result: str) -> bool:
    head = (result or "").strip().lower()
    return not head or head.startswith(_ERROR_PREFIXES)


def annotate(result: str, fetched_at: float, cls: str, now: float | None = None) -> str:
    """A reused result, with when it was fetched, for the model to say."""
    age = max(0, int((now if now is not None else _now()) - fetched_at))
    when = datetime.fromtimestamp(fetched_at).strftime("%H:%M")
    ago = f"{age} s" if age < 120 else f"{age // 60} min"
    return f"[Saved result from {when} ({ago} ago, {cls} data); fetch again if it must be current]\n{result}"


async def call(
    tool: str,
    payload: dict | None,
    *,
    user: str,
    fetch: Callable[[], Awaitable[str]],
    conversation: str | None = None,
) -> str:
    """``fetch()``'s result for this call, reused while its freshness allows."""
    declared, public, tags, is_write = tr.freshness_of(tool, payload)
    if is_write:
        result = await fetch()
        for tag in tags:
            # redis-py is synchronous; a slow or unreachable Redis must never
            # stall the gateway's event loop while a household turn waits.
            await asyncio.to_thread(invalidate_tag, tag, reason=f"write via {tool}")
        await asyncio.to_thread(_stat, tool, writes=1)
        return result
    if not ENABLED or declared == tr.LIVE:
        # Never a Redis round trip on a live call: device commands and safety reads
        # go straight to their source, whatever state the cache is in.
        return await fetch()

    cls = await asyncio.to_thread(effective_class, tool, declared)
    if cls == tr.LIVE:
        return await fetch()
    conversation = conversation or CONVERSATION.get()
    args = normalize_args(payload)
    key = _entry_key(tool, args, user=user, conversation=conversation, public=public)
    now = _now()
    try:
        raw = await asyncio.to_thread(lambda: _redis().get(key))
    except Exception as exc:
        _mark_down(exc)
        raw = None
    if raw:
        try:
            entry = json.loads(raw)
            if now < entry["expires_at"]:
                await asyncio.to_thread(_stat, tool, hits=1,
                                        saved_ms=float(entry.get("source_ms", 0.0)) or 0)
                await asyncio.to_thread(_touch, key, entry, public=public, user=user)
                return annotate(entry["result"], entry["fetched_at"], entry["cls"], now)
        except (ValueError, KeyError, TypeError):
            pass

    started = time.perf_counter()
    result = await fetch()
    source_ms = round((time.perf_counter() - started) * 1000.0, 1)
    await asyncio.to_thread(_stat, tool, misses=1, source_ms=source_ms or 0, source_calls=1)
    if _looks_failed(result) or len(result) > MAX_VALUE_CHARS:
        return result
    await asyncio.to_thread(_remember, key, tool, args, payload, result, cls=cls, tags=tags,
                            public=public, user=user, source_ms=source_ms, now=now)
    return result


def _remember(key: str, tool: str, args: str, payload: dict | None, result: str, *, cls: str,
              tags: tuple[str, ...], public: bool, user: str, source_ms: float, now: float) -> None:
    ttl = ttl_for(cls, now)
    if not public:
        ttl = min(ttl, CONVERSATION_IDLE_S)
    if ttl <= 0:
        return
    digest = _digest(result)
    entry = {
        "tool": tool, "cls": cls, "result": result, "fetched_at": now, "expires_at": now + ttl,
        "ttl": ttl, "tags": list(tags), "source_ms": source_ms, "digest": digest, "uses": 0,
        # Only a household-public entry is refreshed in the background, so only it keeps its call.
        **({"payload": {k: v for k, v in (payload or {}).items() if k != "user_context"}} if public else {}),
    }
    try:
        r = _redis()
        # Pattern learning: did the value change since the last time these arguments were fetched?
        last_key = f"{PREFIX}:last:{tool}:{_digest(args)}"
        previous = r.get(last_key)
        pipe = r.pipeline()
        if previous:
            outcome = "same" if previous == digest else "changed"
            pipe.hincrby(f"{PREFIX}:stats:{tool}", outcome, 1)
            pipe.hincrby(f"{PREFIX}:pattern:{tool}", f"{_digest(args)}:{outcome}", 1)
        pipe.set(last_key, digest, ex=max(ttl_for(tr.STATIC, now), ttl) * 2)
        pipe.set(key, json.dumps(entry), ex=ttl)
        for tag in tags:
            pipe.sadd(f"{PREFIX}:tag:{tag}", key)
            pipe.expire(f"{PREFIX}:tag:{tag}", STATIC_S * 2)
        index = f"{PREFIX}:pubindex" if public else f"{PREFIX}:index:{_user_key(user)}"
        pipe.zadd(index, {key: now})
        pipe.execute()
        _bound(index)
    except Exception as exc:
        _mark_down(exc)


def _touch(key: str, entry: dict, *, public: bool, user: str) -> None:
    """Count a use; a conversation entry lives on for CONVERSATION_IDLE_S after it."""
    try:
        r = _redis()
        entry["uses"] = int(entry.get("uses", 0)) + 1
        remaining = int(entry["expires_at"] - _now())
        if remaining > 0:
            r.set(key, json.dumps(entry), ex=remaining)
        index = f"{PREFIX}:pubindex" if public else f"{PREFIX}:index:{_user_key(user)}"
        r.zadd(index, {key: _now()})
        if public:
            r.zincrby(f"{PREFIX}:hot", 1, key)
    except Exception as exc:
        _mark_down(exc)


def _bound(index: str) -> None:
    """Keep at most MAX_ENTRIES_PER_USER entries per user (least recently used go first)."""
    r = _redis()
    extra = r.zcard(index) - MAX_ENTRIES_PER_USER
    if extra > 0:
        oldest = r.zrange(index, 0, extra - 1)
        if oldest:
            r.delete(*oldest)
            r.zrem(index, *oldest)


def invalidate_tag(tag: str, *, reason: str = "") -> int:
    """Drop every cached entry carrying ``tag``. Returns how many were dropped."""
    try:
        r = _redis()
        keys = list(r.smembers(f"{PREFIX}:tag:{tag}"))
        if keys:
            r.delete(*keys)
        r.delete(f"{PREFIX}:tag:{tag}")
    except Exception as exc:
        _mark_down(exc)
        _pending_invalidations.add(tag)
        return 0
    if keys:
        log.info("[tool-cache] invalidated %s entries tagged %s (%s)", len(keys), tag, reason or "event")
    return len(keys)


def invalidate_user(user: str) -> int:
    """Drop every entry one user owns (a sign-out, a permission change)."""
    try:
        r = _redis()
        index = f"{PREFIX}:index:{_user_key(user)}"
        keys = list(r.zrange(index, 0, -1))
        if keys:
            r.delete(*keys)
        r.delete(index)
        return len(keys)
    except Exception as exc:
        log.warning("[tool-cache] could not drop a user's entries: %s", exc)
        return 0


# --- learning ------------------------------------------------------------------------------

def _stats_for(tool: str) -> dict[str, float]:
    raw = _redis().hgetall(f"{PREFIX}:stats:{tool}") or {}
    return {k: float(v) for k, v in raw.items()}


def suggest() -> list[dict[str, Any]]:
    """For each cached tool: move to a longer class (refetches keep finding the same value) or a
    shorter one (they keep finding a changed one). Live tools are never suggested anything."""
    out: list[dict[str, Any]] = []
    order = list(tr.FRESHNESS_CLASSES)
    for tool, spec in sorted(tr.FRESHNESS.items()):
        declared = {spec.default, *spec.reads.values(), *spec.by_collection.values()} - {tr.LIVE}
        if not declared:
            continue
        current = effective_class(tool, max(declared, key=order.index))
        stats = _stats_for(tool)
        same, changed = stats.get("same", 0.0), stats.get("changed", 0.0)
        samples = same + changed
        if samples < TUNE_MIN_SAMPLES:
            continue
        idx = order.index(current)
        if same / samples >= TUNE_PROMOTE_SAME and idx + 1 <= order.index(MAX_CLASS):
            out.append({"tool": tool, "from": current, "to": order[idx + 1], "samples": int(samples),
                        "reason": f"{same / samples:.0%} of refetches found the same value"})
        elif changed / samples >= TUNE_DEMOTE_CHANGED and idx > 1:
            out.append({"tool": tool, "from": current, "to": order[idx - 1], "samples": int(samples),
                        "reason": f"{changed / samples:.0%} of refetches found a changed value"})
    return out


def tune(*, apply: bool | None = None) -> list[dict[str, Any]]:
    """Run ``suggest`` and, when auto-tuning is on, apply each change (logged, bounded).

    A class change resets the tool's same/changed counts, so the next decision is made on
    evidence gathered under the new class.
    """
    apply = AUTO_TUNE if apply is None else apply
    suggestions = suggest()
    if not apply:
        return suggestions
    r = _redis()
    for s in suggestions:
        if s["to"] == tr.LIVE or s["from"] == tr.LIVE:
            continue
        r.hset(f"{PREFIX}:overrides", s["tool"], s["to"])
        r.hdel(f"{PREFIX}:stats:{s['tool']}", "same", "changed")
        record = {**s, "at": _now(), "applied": True}
        r.lpush(f"{PREFIX}:tuning_log", json.dumps(record))
        r.ltrim(f"{PREFIX}:tuning_log", 0, TUNING_LOG_MAX - 1)
        log.info("[tool-cache] %s: %s -> %s (%s, %s samples)", s["tool"], s["from"], s["to"], s["reason"], s["samples"])
        s["applied"] = True
    return suggestions


def clear_override(tool: str) -> None:
    _redis().hdel(f"{PREFIX}:overrides", tool)


# --- keeping hot household data warm -------------------------------------------------------

async def refresh_hot(fetcher: Callable[[str, dict], Awaitable[str]]) -> int:
    """Refetch hot household-public entries in the last ``REFRESH_BEFORE`` of their life.

    ``fetcher(tool, payload)`` asks the source directly (never the cache). Returns the
    number refreshed; at most REFRESH_MAX_PER_CYCLE per call.
    """
    r = _redis()
    refreshed = 0
    for key, uses in r.zrevrange(f"{PREFIX}:hot", 0, -1, withscores=True):
        if refreshed >= REFRESH_MAX_PER_CYCLE or uses < HOT_USES:
            break
        raw = r.get(key)
        if not raw:
            r.zrem(f"{PREFIX}:hot", key)
            continue
        entry = json.loads(raw)
        now = _now()
        left = entry["expires_at"] - now
        if left > entry["ttl"] * REFRESH_BEFORE or "payload" not in entry:
            continue
        started = time.perf_counter()
        result = await fetcher(entry["tool"], entry["payload"])
        if _looks_failed(result):
            log.warning("[tool-cache] refresh of %s failed; the entry expires as planned", entry["tool"])
            continue
        source_ms = round((time.perf_counter() - started) * 1000.0, 1)
        cls = effective_class(entry["tool"], entry["cls"])
        ttl = ttl_for(cls, now)
        same = _digest(result) == entry["digest"]
        entry.update(result=result, fetched_at=now, expires_at=now + ttl, ttl=ttl, source_ms=source_ms,
                     digest=_digest(result), cls=cls, uses=0)
        pipe = r.pipeline()
        pipe.set(key, json.dumps(entry), ex=ttl)
        pipe.zadd(f"{PREFIX}:hot", {key: 0})
        pipe.hincrby(f"{PREFIX}:stats:{entry['tool']}", "refreshes", 1)
        pipe.hincrby(f"{PREFIX}:stats:{entry['tool']}", "same" if same else "changed", 1)
        pipe.execute()
        refreshed += 1
    return refreshed


# --- the admin view -------------------------------------------------------------------------

def report() -> dict[str, Any]:
    """Hit rate, time saved and tuning, per tool."""
    r = _redis()
    tools: dict[str, Any] = {}
    for key in sorted(r.scan_iter(f"{PREFIX}:stats:*")):
        tool = key.split(":", 3)[3]
        s = _stats_for(tool)
        lookups = s.get("hits", 0) + s.get("misses", 0)
        calls = s.get("source_calls", 0)
        tools[tool] = {
            **{k: int(v) if float(v).is_integer() else round(v, 1) for k, v in s.items()},
            "hit_rate": round(s.get("hits", 0) / lookups, 3) if lookups else None,
            "avg_source_ms": round(s.get("source_ms", 0) / calls, 1) if calls else None,
            "unchanged_on_refetch": round(s.get("same", 0) / (s.get("same", 0) + s.get("changed", 0)), 3)
            if (s.get("same", 0) + s.get("changed", 0)) else None,
        }
    return {
        "enabled": ENABLED,
        "auto_tune": AUTO_TUNE,
        "max_class": MAX_CLASS,
        "classes": {"short_s": SHORT_S, "daily": "until local midnight", "static_s": STATIC_S,
                    "conversation_idle_s": CONVERSATION_IDLE_S},
        "declared": {
            tool: {"default": spec.default, "reads": spec.reads, "by_collection": spec.by_collection,
                   "household_public": spec.household_public, "tags": list(spec.tags)}
            for tool, spec in sorted(tr.FRESHNESS.items())
        },
        "overrides": r.hgetall(f"{PREFIX}:overrides") or {},
        "suggestions": suggest(),
        "tuning_log": [json.loads(x) for x in r.lrange(f"{PREFIX}:tuning_log", 0, 19)],
        "tools": tools,
    }


# --- the background job ---------------------------------------------------------------------

#: How often hot entries are checked for refresh, and how often tuning runs.
REFRESH_EVERY_S = _bounded("TOOL_CACHE_REFRESH_EVERY_S", 60, 10, 3600, int)
TUNE_EVERY_S = _bounded("TOOL_CACHE_TUNE_EVERY_S", 900, 60, 86400, int)


async def _household_fetch(tool: str, payload: dict) -> str:
    """Fetch a household-public entry again, straight from its source (not the cache)."""
    from services.gateway import orchestrator
    from services.gateway.schemas import ResolvedCredentials

    return await orchestrator._run_single_tool(
        tool, {"action": tool, "payload": dict(payload)}, "", ResolvedCredentials(user="household")
    )


async def maintain(passes: int | None = None, *, sleep_s: float | None = None) -> None:
    """Keep hot household entries warm and tune classes, until cancelled.

    Each pass is contained: a failure is logged and the next pass runs as planned.
    """
    import asyncio

    done = 0
    last_tune = _now()
    while passes is None or done < passes:
        done += 1
        await asyncio.sleep(REFRESH_EVERY_S if sleep_s is None else sleep_s)
        if not ENABLED:
            continue
        try:
            n = await refresh_hot(_household_fetch)
            if n:
                log.info("[tool-cache] refreshed %s hot entries", n)
        except Exception as exc:
            log.warning("[tool-cache] refresh pass failed: %s: %s", type(exc).__name__, exc)
        if _now() - last_tune >= TUNE_EVERY_S:
            last_tune = _now()
            try:
                tune()
            except Exception as exc:
                log.warning("[tool-cache] tuning pass failed: %s: %s", type(exc).__name__, exc)
