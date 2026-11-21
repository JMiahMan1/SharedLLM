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
    from services.gateway import private_chat

    # A private chat only takes messages encrypted on the device: an inline
    # reply would post readable text into it (chat_notify offers no Reply there).
    private_chat.guard_talk(token, "send", text)
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
    # The fired timer: if it rang on a voice satellite, the satellite stops it and shows the new one.
    "again_of",
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


# Home (the device hub's notify.mobile_app_* sends these when a phone has no companion app push channel)

#: Home Assistant's event for a tapped notification button; automations and blueprints
#: (confirmable_notification) wait for it with the action id they sent.
HOME_ACTION_EVENT = "mobile_app_notification_action"
_HOME_ACTION_MAX = 255


@push_callback("home_notification_action")
async def home_notification_action(request: Request, params: dict, reply: str | None) -> dict:
    """Fire ``mobile_app_notification_action`` in the device hub with the button's action id, the
    notice's tag and, for a reply button, ``reply_text``, as the companion app does in Home
    Assistant. ``params`` is ``{"action", "tag"?}`` (services/homehub/drivers/mobile_app.py)."""
    from services.gateway.phone_routes import _hub

    action = params.get("action")
    if not isinstance(action, str) or not action.strip() or len(action) > _HOME_ACTION_MAX:
        raise HTTPException(status_code=422, detail="params.action is required")
    data: dict[str, Any] = {"action": action}
    tag = params.get("tag")
    if tag is not None:
        if not isinstance(tag, str) or len(tag) > _HOME_ACTION_MAX:
            raise HTTPException(status_code=422, detail="params.tag must be text")
        data["tag"] = tag
    if reply is not None:
        data["reply_text"] = reply
    status, _body = await _hub("POST", f"/api/events/{HOME_ACTION_EVENT}", json=data)
    if status != 200:
        raise HTTPException(status_code=502, detail="The device hub did not take the answer")
    return {"status": "SUCCESS", "message": "Sent."}


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


@push_callback("guard_unlock_decide")
async def guard_unlock_decide(request: Request, params: dict, reply: str | None) -> dict:
    """A parent taps Approve (for the minutes asked) or Deny on a locked
    device's unlock request, through the same path as
    ``POST /api/guard/unlock-requests/{id}``, as the caller: ``params`` is
    ``{"id", "approve", "minutes"?}``. Identity refuses anyone who is not
    this child's parent, and any device key."""
    from services.gateway.main import decide_unlock_request

    try:
        request_id = int(params.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="params.id is required") from None
    approve = params.get("approve")
    if not isinstance(approve, bool):
        raise HTTPException(status_code=422, detail="params.approve must be true or false")
    payload: dict = {"approve": approve}
    if approve and params.get("minutes") is not None:
        payload["minutes"] = params.get("minutes")
    status, body = await decide_unlock_request(request, request_id, payload)
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


@push_callback("chore_check_decide")
async def chore_check_decide(request: Request, params: dict, reply: str | None) -> dict:
    """A parent taps "Done right" / "Redo it" on a ticked-off chore, through the
    same path as ``POST /api/screen-time/chore-checks/{id}``, as the caller:
    ``params`` is ``{"id", "approve"}``. Identity checks that the caller may answer."""
    from services.gateway.main import decide_chore_check

    try:
        check_id = int(params.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="params.id is required") from None
    status, body = await decide_chore_check(request, check_id, {"approve": bool(params.get("approve"))})
    if status != 200:
        raise HTTPException(status_code=status, detail=body.get("detail") or "Could not answer")
    return body


# --------------------------------------------------------------------------- Garden push callbacks (Phase 2)
#
# The morning digest surfaces garden_done / garden_snooze inline buttons.  A tap
# calls these handlers as the person who tapped (never spoofable), proxying to
# the Garden service with their resolved identity headers.

async def _garden_proxy(
    request: Request,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
) -> dict:
    """Proxy a Garden service call as the already-authenticated caller.

    Reuses ``garden_routes._verified_headers`` (resolves identity from Bearer
    token only — never spoofable client headers) and ``garden_routes._call``
    (passes through all status codes).
    """
    from services.gateway import garden_routes

    headers = await garden_routes._verified_headers(request)
    response = await garden_routes._call(method, path, params=params, json=json, headers=headers)
    # Decode here: the callers below build their own JSON answer, and a Starlette
    # ``Response`` inside it is not serializable -- every digest tap used to 500
    # with "Object of type Response is not JSON serializable".
    body = garden_routes._decode(response)
    status = int(getattr(response, "status_code", 200) or 200)
    if status >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(status_code=status, detail=detail or "The garden service refused that")
    return body if isinstance(body, dict) else {"result": body}


@push_callback("garden_done")
async def garden_done(request: Request, params: dict, reply: str | None) -> dict:
    """Inline "Watered" button from the morning digest: log care as the tapper.

    ``params`` is ``{"plant_id", "action"}`` or ``{"plant_ids": [...], "action": "..."}``.
    """
    plant_id = params.get("plant_id")
    plant_ids = params.get("plant_ids")
    action = params.get("action", "water")
    if plant_ids:
        results = []
        for pid in plant_ids:
            try:
                results.append(
                    await _garden_proxy(
                        request,
                        "POST",
                        f"/plants/{pid}/care",
                        json={"action": action, "source": "push_action"},
                    )
                )
            except HTTPException:
                results.append({"plant_id": pid, "error": "failed"})
        return {"results": results}
    if not plant_id:
        raise HTTPException(status_code=422, detail="params.plant_id or params.plant_ids is required")
    return await _garden_proxy(
        request,
        "POST",
        f"/plants/{plant_id}/care",
        json={"action": action, "source": "push_action"},
    )


@push_callback("garden_snooze")
async def garden_snooze(request: Request, params: dict, reply: str | None) -> dict:
    """Inline "Still damp (+2d)" button from the morning digest: snooze as the tapper.

    ``params`` is ``{"plant_id", "postpone_days"?}`` or
    ``{"plant_ids": [...], "postpone_days"?}``.
    """
    plant_id = params.get("plant_id")
    plant_ids = params.get("plant_ids")
    postpone_days = str(params.get("postpone_days", 2))
    if plant_ids:
        results = []
        for pid in plant_ids:
            try:
                results.append(
                    await _garden_proxy(
                        request,
                        "POST",
                        f"/plants/{pid}/snooze",
                        params={"still_damp": "true", "postpone_days": postpone_days},
                    )
                )
            except HTTPException:
                results.append({"plant_id": pid, "error": "failed"})
        return {"results": results}
    if not plant_id:
        raise HTTPException(status_code=422, detail="params.plant_id or params.plant_ids is required")
    return await _garden_proxy(
        request,
        "POST",
        f"/plants/{plant_id}/snooze",
        params={"still_damp": "true", "postpone_days": postpone_days},
    )
