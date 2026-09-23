# services/telemetry/alpaca.py
"""Alpaca admission control.

Report runs must not disturb whatever Alpaca is already doing (voice, chat,
Raven). Two layers:

1. Pre-check ``/admin/slots`` before starting a run. If every slot is busy the
   run is deferred without consuming an attempt.
2. When the run does start, pass ``queue_timeout`` so Alpaca's own shared slot
   queue holds the request instead of rejecting it — that covers the race where
   a slot frees up right after the pre-check.
"""
from __future__ import annotations

import logging
import os

import aiohttp

from services.telemetry.config import INTERNAL_SECRET

log = logging.getLogger("telemetry.alpaca")

_client: aiohttp.ClientSession | None = None


def _get_client() -> aiohttp.ClientSession:
    global _client
    if _client is None or _client.closed:
        _client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
    return _client


def base_url() -> str:
    url = (
        os.getenv("LLM_LOCAL_URL")
        or os.getenv("OLLAMA_URL")
        or "http://ollama:11434"
    ).rstrip("/")
    return url


def _slots_from_payload(payload) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("slots", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
            if isinstance(value, dict):
                return [s for s in value.values() if isinstance(s, dict)]
        if "available" in payload or "total" in payload:
            return [payload]
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    return []


def _slot_is_busy(slot: dict) -> bool:
    if isinstance(slot.get("is_processing"), bool):
        return slot["is_processing"]
    state = slot.get("state")
    if isinstance(state, bool):
        return state
    if isinstance(state, int):
        return state != 0
    if isinstance(state, str):
        return state.lower() not in ("idle", "available", "ready")
    return False


def evaluate_slots(payload) -> tuple[bool, str]:
    """Return (busy, reason) for an /admin/slots or /slots payload."""
    slots = _slots_from_payload(payload)
    if not slots:
        # Unknown/absent slot info: treat as busy so we defer rather than
        # risk contending with a live task.
        return True, "no slot information available"
    if any(_slot_is_busy(s) for s in slots):
        return True, f"{sum(1 for s in slots if _slot_is_busy(s))}/{len(slots)} slots busy"
    return False, "slots idle"


async def is_busy(model: str | None = None) -> tuple[bool, str]:
    """Ask Alpaca whether it can accept work right now."""
    url = f"{base_url()}/admin/slots"
    if model:
        url = f"{url}?model={model}"
    try:
        async with _get_client().get(
            url,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as resp:
            if resp.status != 200:
                return True, f"slots endpoint returned {resp.status}"
            return evaluate_slots(await resp.json(content_type=None))
    except Exception as e:
        return True, f"could not reach alpaca slots endpoint: {type(e).__name__}"
