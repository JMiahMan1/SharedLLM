"""Notification buttons the phone answers: the fixed ``PUSH_CALLBACKS`` list.

A notice's ``callback`` action (docs/PUSH.md) makes the phone call
``POST /api/push/action`` with ``{"callback", "params", "reply"?}`` and the
person's own API key. Only names registered here are accepted. A callback is

    async def handler(request: Request, params: dict, reply: str | None) -> dict | Response

and acts as the caller (``request`` carries their key), never as the service.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request

PushCallback = Callable[[Request, dict, str | None], Awaitable[Any]]
PUSH_CALLBACKS: dict[str, PushCallback] = {}


def push_callback(name: str):
    """Register ``handler`` under ``name``."""
    def register(handler: PushCallback) -> PushCallback:
        PUSH_CALLBACKS[name] = handler
        return handler
    return register


# Chat (chat_notify sends Reply on a new message)


@push_callback("chat_reply")
async def chat_reply(request: Request, params: dict, reply: str | None):
    """Inline reply: post the text into Talk conversation ``params.token`` as
    the person who typed it, through the same send as the chat page."""
    from services.gateway.main import _proxy_execution_with_identity

    token = str(params.get("token") or "").strip()
    text = (reply or "").strip()
    if not token or not text:
        raise HTTPException(status_code=422, detail="params.token and reply text are required")
    return await _proxy_execution_with_identity(
        request, "/execute/talk", {"action": "send", "token": token, "message": text}
    )


# Timers (execution handlers/timer.py notify_timer_done sends these buttons)

#: A snooze or an extra minute is a new timer with the same name, speakers,
#: sound, emoji and link (a snoozed note reminder still opens the note).
_AGAIN_FIELDS = (
    "title",
    "target_device",
    "target_devices",
    "emoji",
    "sound",
    "esphome_script",
    "flash_screen",
    "scope",
    "tts_enabled",
    "url",
)


async def _execute_timer(request: Request, payload: dict) -> dict:
    """``/execute/timer`` as the caller, the same route the Communication page uses."""
    from services.gateway.main import _proxy_execution_with_identity

    resp = await _proxy_execution_with_identity(request, "/execute/timer", payload)
    try:
        return json.loads(resp.body)
    except ValueError:
        return {"status": "FAILURE", "message": "Execution gave no answer"}


async def _again(request: Request, params: dict, duration: str) -> dict:
    kind = params.get("type") if params.get("type") in ("alarm", "reminder") else "timer"
    payload = {"action": "add", "type": kind, "duration_str": duration}
    payload.update({k: params[k] for k in _AGAIN_FIELDS if params.get(k) is not None and params.get(k) != ""})
    return await _execute_timer(request, payload)


@push_callback("timer_stop")
async def timer_stop(request: Request, params: dict, reply: str | None) -> dict:
    """Stop ringing. A one-off timer is normally gone once it fires; one the
    scheduler is still retrying is deleted so it cannot ring again. A
    recurring alarm sends no id and stays scheduled."""
    if params.get("device"):
        return await _execute_timer(
            request,
            {
                "action": "stop",
                "id": str(params["id"]) if params.get("id") else None,
                "target_device": str(params["device"]),
            },
        )
    if not params.get("id"):
        return {"status": "SUCCESS", "message": "Stopped."}
    result = await _execute_timer(request, {"action": "delete", "id": str(params["id"])})
    if result.get("status") != "SUCCESS" and "No matching timer" in str(result.get("message")):
        return {"status": "SUCCESS", "message": "Stopped."}
    return result


@push_callback("timer_snooze")
async def timer_snooze(request: Request, params: dict, reply: str | None) -> dict:
    return await _again(request, params, "5m")


@push_callback("timer_add_minute")
async def timer_add_minute(request: Request, params: dict, reply: str | None) -> dict:
    return await _again(request, params, "1m")


# Raven (notices.mission_needs_approval sends these buttons)


async def _raven_creds(request: Request) -> dict:
    from services.gateway.main import _resolve_identity_from_request

    return await _resolve_identity_from_request(request)


@push_callback("raven_approve")
async def raven_approve(request: Request, params: dict, reply: str | None) -> dict:
    """Run a mission waiting in Raven Ops (admins only, as Execute there)."""
    from services.gateway import raven_approval

    return await raven_approval.approve(await _raven_creds(request), params)


@push_callback("raven_reject")
async def raven_reject(request: Request, params: dict, reply: str | None) -> dict:
    """Drop a mission waiting in Raven Ops (admins only, as Delete there)."""
    from services.gateway import raven_approval

    return await raven_approval.reject(await _raven_creds(request), params)


# Family guard (the phone's "… is blocked" notice: Ask a parent)


@push_callback("guard_ask")
async def guard_ask(request: Request, params: dict, reply: str | None) -> dict:
    """A child asks a parent first from a notification, through the same path
    as ``POST /api/guard/requests``: ``params`` is ``{"kind", "target"}``."""
    from services.gateway.main import ask_a_parent

    status, body = await ask_a_parent(request, {"kind": params.get("kind"), "target": params.get("target")})
    if status != 200:
        raise HTTPException(status_code=status, detail=body.get("detail") or "Could not ask")
    return body


@push_callback("guard_decide")
async def guard_decide(request: Request, params: dict, reply: str | None) -> dict:
    """A parent taps Approve / Disapprove on a child's request, through the
    same path as ``POST /api/guard/requests/{id}``, as the caller: ``params``
    is ``{"id", "approve"}``. A parent who is not this child's parent gets
    identity's own refusal."""
    from services.gateway.main import decide_guard_request

    try:
        request_id = int(params.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="params.id is required") from None
    status, body = await decide_guard_request(request, request_id, {"approve": bool(params.get("approve"))})
    if status != 200:
        raise HTTPException(status_code=status, detail=body.get("detail") or "Could not answer")
    return body


@push_callback("more_time_decide")
async def more_time_decide(request: Request, params: dict, reply: str | None) -> dict:
    """A parent taps Approve / Disapprove on a request for more screen time,
    through the same path as ``POST /api/screen-time/requests/{id}``, as the
    caller: ``params`` is ``{"id", "grant"}``. Identity checks that the caller
    may answer."""
    from services.gateway.main import decide_more_time

    try:
        request_id = int(params.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="params.id is required") from None
    status, body = await decide_more_time(request, request_id, {"grant": bool(params.get("grant"))})
    if status != 200:
        raise HTTPException(status_code=status, detail=body.get("detail") or "Could not answer")
    return body
