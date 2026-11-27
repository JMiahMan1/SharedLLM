"""App vetting from the app: the child's device reports its apps, the parents
see each app's rating and the local review of it (docs/APP_VETTING.md).

* ``POST /api/apps/inventory`` - a child's or teen's phone or tablet reports
  its installed apps (signed in as the child). An adult's device is refused:
  their apps are nobody's business here.
* ``GET /api/guardian/apps/{username}`` - the Apps card: every installed app
  with its verdict (``app_vetting.assess``), questionable ones first for the
  asking parent's notify level, and the apps not used lately. Identity decides
  who may see it (the child's parents and admins, as for their concerns); the
  child never does.
* ``PUT /api/guardian/apps/{username}/policy``, ``.../apps/{package}``,
  ``POST .../dismiss``, ``POST .../action`` - the age rule, a parent's per-app
  choice, "reviewed, keep", and block / remove; identity checks and stores.
* ``vet_requests`` and ``request_verdict`` put the verdict on a child's "ask a
  parent" app requests (the request card and the parent's notice).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.common import app_security, app_vetting
from services.common.http import get_client
from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET, TELEMETRY_SVC

log = logging.getLogger("gateway.app_vetting")

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=15)
MINOR_ROLES = ("child", "teen")
MAX_INVENTORY_APPS = 500
_DEVICE_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
#: Jarvis's own Android package, never listed among a child's apps.
JARVIS_PACKAGE = "com.jarvisos.app"
REQUEST_KINDS = ("app", "app_unblock")


async def _caller(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return str(await _require_authenticated(request)).strip().lower()


def _caller_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in (("Authorization", request.headers.get("Authorization")),
                              ("X-API-Key", request.headers.get("X-API-Key"))) if v}


async def _call(method: str, url: str, headers: dict[str, str], **kwargs: Any) -> tuple[int, Any]:
    async with get_client().request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"detail": (await resp.text())[:300]}
        return resp.status, body


async def identity_call(method: str, path: str, request: Request | None = None, **kwargs: Any) -> tuple[int, Any]:
    """Identity, as the caller when ``request`` is given (its rules apply),
    else with the internal secret."""
    headers = _caller_headers(request) if request is not None else {"X-Internal-Secret": INTERNAL_SECRET}
    return await _call(method, f"{IDENTITY_SVC}{path}", headers, **kwargs)


async def telemetry_call(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    return await _call(method, f"{TELEMETRY_SVC}{path}", {"X-Internal-Secret": INTERNAL_SECRET}, **kwargs)


def _relay(status: int, body: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content=body if isinstance(body, (dict, list)) else {"detail": str(body)})


# --- the device reports ------------------------------------------------------


@router.post("/api/apps/inventory")
async def report_inventory(request: Request):
    """A minor's device reports its installed apps: ``{"device_key", "apps":
    [{package, label, installer, first_install, last_update, last_used,
    launchable, permissions, size_bytes, capabilities, special_access,
    cert_sha1, apk_sha256, apk_hash_state}], "usage_access"}``."""
    user = await _caller(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send an object")
    status, role = await identity_call("GET", f"/api/internal/family-role/{user}")
    if status != 200:
        raise HTTPException(status_code=503, detail="Could not check whose device this is")
    if (role or {}).get("family_role") not in MINOR_ROLES:
        raise HTTPException(status_code=403, detail="Only a child's or teen's device reports its apps")
    device = str(body.get("device_key") or "").strip()
    if not _DEVICE_KEY.fullmatch(device):
        raise HTTPException(status_code=422, detail="device_key must be this device's key")
    apps = body.get("apps")
    if not isinstance(apps, list) or len(apps) > MAX_INVENTORY_APPS:
        raise HTTPException(status_code=422, detail=f"apps must be a list of at most {MAX_INVENTORY_APPS}")
    status, kept = await telemetry_call("POST", "/api/telemetry/apps/inventory", json={
        "user": user, "device": device, "apps": apps, "usage_access": bool(body.get("usage_access")),
    })
    if status != 200:
        raise HTTPException(status_code=502, detail="The app list could not be kept just now")
    return {"status": "SUCCESS", "apps": (kept or {}).get("apps", 0)}


# --- the parents' Apps card ---------------------------------------------------


def _row(app: dict[str, Any], verdict: dict[str, Any], notify_level: str) -> dict[str, Any]:
    attention = [] if verdict["dismissed"] else app_vetting.flagged_for(verdict, notify_level)
    return {
        **verdict,
        "installer": app.get("installer"),
        "first_install": app.get("first_install"),
        "last_update": app.get("last_update"),
        "last_used": app.get("last_used"),
        "permissions": app.get("permissions") or [],
        "size_bytes": app.get("size_bytes"),
        "devices": app.get("devices") or [],
        "special_access": app.get("special_access") or [],
        "capabilities": app.get("capabilities") or [],
        "attention": attention,
    }


def build_card(policy: dict[str, Any], data: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """The Apps card from identity's policy and telemetry's apps (pure)."""
    level = policy.get("notify_level") or "high"
    catalog = data.get("catalog") or {}
    apps = [a for a in data.get("apps") or [] if a.get("package") != JARVIS_PACKAGE]
    by_package = {a["package"]: a for a in apps}
    verdicts = [app_vetting.assess(a, catalog.get(a["package"]), policy, JARVIS_PACKAGE) for a in apps]
    ordered = app_vetting.sort_for_parent(verdicts, level)
    rows = [_row(by_package[v["package"]], v, level) for v in ordered]
    return {
        "username": policy.get("username"),
        "policy": policy,
        "devices": data.get("devices") or [],
        "apps": rows,
        "attention": sum(1 for r in rows if r["attention"]),
        "unused": app_vetting.unused_apps(apps, policy, now_ms, JARVIS_PACKAGE),
        "lookups": data.get("lookups") or {},
        "stalkerware_list": app_security.stalkerware_source(),
    }


@router.get("/api/guardian/apps/{username}")
async def guardian_apps(request: Request, username: str):
    await _caller(request)
    status, policy = await identity_call("GET", f"/api/apps/{username}/policy", request)
    if status != 200:
        return _relay(status, policy)
    status, data = await telemetry_call("GET", f"/api/telemetry/apps/{policy['username']}")
    if status != 200:
        raise HTTPException(status_code=502, detail="The app list could not be loaded just now")
    return build_card(policy, data, int(time.time() * 1000))


@router.put("/api/guardian/apps/{username}/policy")
async def put_policy(request: Request, username: str):
    await _caller(request)
    return _relay(*await identity_call("PUT", f"/api/apps/{username}/policy", request, json=await request.json()))


@router.put("/api/guardian/apps/{username}/apps/{package}")
async def put_override(request: Request, username: str, package: str):
    await _caller(request)
    return _relay(*await identity_call("PUT", f"/api/apps/{username}/policy/apps/{package}", request,
                                       json=await request.json()))


@router.post("/api/guardian/apps/{username}/dismiss")
async def dismiss(request: Request, username: str):
    await _caller(request)
    return _relay(*await identity_call("POST", f"/api/apps/{username}/policy/dismiss", request, json=await request.json()))


@router.post("/api/guardian/apps/{username}/action")
async def action(request: Request, username: str):
    await _caller(request)
    return _relay(*await identity_call("POST", f"/api/apps/{username}/policy/action", request, json=await request.json()))


# --- verdicts on "ask a parent" requests ------------------------------------


def _request_summary(verdict: dict[str, Any]) -> dict[str, Any]:
    """What a request card shows (and nothing about the child's other apps)."""
    keys = ("lookup", "lookup_reason", "review_state", "review_reason", "title", "developer", "rating", "limit",
            "over_age", "ads", "in_app_purchases", "interactive", "summary", "level", "stars", "installs",
            "age_source", "common_sense", "review_excerpts")
    return {
        **{k: verdict.get(k) for k in keys},
        "concerns": [{"label": c["label"], "severity": c["severity"], "why": c.get("why")} for c in verdict["concerns"]],
        # The security scan on the app's own profile (sideloaded, risky access,
        # stalkerware, MalwareBazaar), most serious first.
        "security": [{"text": f["text"], "severity": f["severity"], "check": f["check"]}
                     for f in verdict.get("security") or []],
        "security_level": verdict.get("security_level"),
        "line": app_vetting.verdict_line(verdict),
    }


async def _children() -> dict[str, dict[str, Any]]:
    status, body = await identity_call("GET", "/api/internal/guardian/children")
    if status != 200:
        raise RuntimeError(f"identity answered HTTP {status}")
    return {c["username"]: c for c in (body or {}).get("children") or [] if c.get("username")}


def _child_policy(child: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    return app_vetting.effective_policy(child.get("app_policy"), child.get("birth_date"), date.today())


async def vet_requests(caller: str, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The caller's visible requests, with a ``vetting`` verdict on each app
    request of a child they look after. A child's own requests carry none.
    When the verdict cannot be worked out the row says why, never a guess."""
    wanted = [r for r in requests if r.get("kind") in REQUEST_KINDS and r.get("username") != caller
              and app_vetting.is_package(r.get("target"))]
    if not wanted:
        return requests
    try:
        children = await _children()
        status, body = await telemetry_call("POST", "/api/telemetry/apps/catalog",
                                            json={"packages": sorted({r["target"] for r in wanted})})
        if status != 200:
            raise RuntimeError(f"telemetry answered HTTP {status}")
        catalog = (body or {}).get("catalog") or {}
    except Exception as exc:
        log.warning("[app-vetting] request verdicts unavailable: %s", exc)
        for row in wanted:
            row["vetting"] = {"lookup": "unavailable", "line": "The app check is not available just now."}
        return requests
    for row in wanted:
        child = children.get(row["username"])
        if child is None:
            continue
        # The phone sends the app's own profile with the question, so the scan
        # (sideload, risky access, stalkerware, MalwareBazaar) and the age rating
        # are worked out from the real app rather than its package name alone.
        profile = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        app = {**{k: v for k, v in profile.items() if k != "package"},
               "package": row["target"], "label": row.get("label") or profile.get("label") or ""}
        verdict = app_vetting.assess(app, catalog.get(row["target"]), _child_policy(child), JARVIS_PACKAGE)
        row["vetting"] = _request_summary(verdict)
    return requests


async def request_verdict(child: str, package: str, label: str, request_id: Any) -> str:
    """The verdict line for a new request's notice. Queues the lookup ahead of
    others and, when it is not in yet, asks telemetry to send the notice again
    once it is ("checking" until then). Never raises: a notice still goes."""
    try:
        children = await _children()
        status, body = await telemetry_call("POST", "/api/telemetry/apps/catalog", json={
            "packages": [package], "waiting": {"child": child, "request_id": request_id, "label": label},
        })
        if status != 200 or child not in children:
            raise RuntimeError(f"telemetry answered HTTP {status}" if status != 200 else "not a monitored child")
        verdict = app_vetting.assess({"package": package, "label": label},
                                     ((body or {}).get("catalog") or {}).get(package), _child_policy(children[child]),
                                     JARVIS_PACKAGE)
        return app_vetting.verdict_line(verdict)
    except Exception as exc:
        log.warning("[app-vetting] verdict for %s's %s unavailable: %s", child, package, exc)
        return "The app check is not available just now."
