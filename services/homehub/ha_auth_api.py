"""HA's ``/auth/*`` routes, so the companion apps can stay logged in (and log in again).

| Route | What |
|---|---|
| ``POST /auth/token`` | ``grant_type=refresh_token`` (the one every app session depends on) or ``authorization_code`` |
| ``POST /auth/revoke`` | ``token=<refresh token>``; always 200, as RFC 7009 asks |
| ``GET /auth/providers`` | The one provider HA had here: ``homeassistant`` |
| ``POST /auth/login_flow`` | Starts a username/password flow (HA's JSON shapes) |
| ``POST /auth/login_flow/{id}`` | ``{"client_id", "username", "password"}`` -> ``create_entry`` with an auth code |
| ``DELETE /auth/login_flow/{id}`` | Abandon a flow |
| ``GET /auth/authorize`` | The page the app opens to log in (HA's frontend is not here, so a small one is) |

Plus the WebSocket command ``auth/current_user``, which the app calls after logging in.
"""
from __future__ import annotations

import html
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from services.homehub.ha_auth import PROVIDER, AuthError, HAAuth, verify_client_id, verify_redirect_uri

log = logging.getLogger("homehub.ha_auth_api")

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
#: Bodies of unauthenticated requests are small; HA caps them the same way.
MAX_BODY = 16 * 1024


def _form_schema() -> list[dict[str, Any]]:
    return [{"name": "username", "required": True, "type": "string"},
            {"name": "password", "required": True, "type": "string"}]


def _form(flow_id: str, errors: dict[str, str] | None = None) -> dict[str, Any]:
    return {"type": "form", "flow_id": flow_id, "handler": ["homeassistant", None], "step_id": "init",
            "data_schema": _form_schema(), "errors": errors or {}, "description_placeholders": None,
            "last_step": None, "preview": None}


async def _read_form(request: Request) -> dict[str, str] | None:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None
    from urllib.parse import parse_qsl

    pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        return None  # RFC 6749 3.2: a parameter given twice is an invalid request
    return dict(pairs)


async def _read_json(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _message(message: str, status: int) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def build_router(runtime: Any, auth: Any) -> APIRouter:
    router = APIRouter()
    ha = HAAuth(runtime.store, auth)
    runtime.ha_auth = ha  # the WebSocket command and tests reach it here

    @router.post("/auth/token")
    async def token(request: Request) -> Response:
        data = await _read_form(request)
        if data is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            if data.get("action") == "revoke":  # IndieAuth's older revocation form
                ha.revoke(data.get("token"))
                return Response(status_code=200)
            grant = data.get("grant_type")
            if grant == "refresh_token":
                body = ha.refresh_grant(data.get("refresh_token"), data.get("client_id"))
            elif grant == "authorization_code":
                body = ha.code_grant(data.get("client_id"), data.get("code"), data.get("code_verifier"))
            else:
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        except AuthError as exc:
            payload = {"error": exc.code}
            if exc.description:
                payload["error_description"] = exc.description
            return JSONResponse(payload, status_code=exc.status)
        return JSONResponse(body, headers=_NO_STORE)

    @router.post("/auth/revoke")
    async def revoke(request: Request) -> Response:
        data = await _read_form(request) or {}
        ha.revoke(data.get("token"))
        return Response(status_code=200)

    @router.get("/auth/providers")
    async def providers() -> dict[str, Any]:
        return {"providers": [PROVIDER], "preselect_remember_me": False}

    def _flow_response(flow_id: str, result: Any) -> dict[str, Any]:
        return {"type": "create_entry", "flow_id": flow_id, "handler": ["homeassistant", None],
                "title": PROVIDER["name"], "description": None, "description_placeholders": None,
                "version": 1, "minor_version": 1, "options": {}, "result": result}

    @router.post("/auth/login_flow")
    async def login_flow(request: Request) -> Response:
        data = await _read_json(request)
        if data is None:
            return _message("Message format incorrect", 400)
        client_id, redirect_uri = str(data.get("client_id") or ""), str(data.get("redirect_uri") or "")
        if not verify_client_id(client_id):
            return _message("Invalid client id", 400)
        if not redirect_uri:
            return _message("Message format incorrect", 400)
        handler = data.get("handler") or []
        if not isinstance(handler, list) or not handler or handler[0] != "homeassistant":
            return _message("Invalid handler specified", 404)
        if data.get("response_type", "code") != "code":
            return _message("Response type not supported", 400)
        challenge, method = data.get("code_challenge"), data.get("code_challenge_method")
        if method is not None and not challenge:
            return _message("code_challenge required when code_challenge_method is provided", 400)
        if challenge is not None and method != "S256":
            return _message("Transform algorithm not supported", 400)
        flow = ha.start_flow(client_id, redirect_uri, challenge)
        return JSONResponse(_form(flow.flow_id))

    @router.post("/auth/login_flow/{flow_id}")
    async def login_step(flow_id: str, request: Request) -> Response:
        data = await _read_json(request)
        if data is None:
            return _message("Message format incorrect", 400)
        flow = ha.get_flow(flow_id)
        if flow is None:
            return _message("Invalid flow specified", 404)
        if data.get("client_id") != flow.client_id:
            return _message("Invalid client id", 400)
        import asyncio

        cred = await asyncio.to_thread(ha.validate_login, str(data.get("username") or ""),
                                       str(data.get("password") or ""))
        if cred is None:
            return JSONResponse(_form(flow_id, {"base": "invalid_auth"}))
        if not verify_redirect_uri(flow.client_id, flow.redirect_uri):
            return _message("Invalid redirect URI", 403)
        return JSONResponse(_flow_response(flow_id, ha.create_code(flow, cred)))

    @router.delete("/auth/login_flow/{flow_id}")
    async def login_abort(flow_id: str) -> Response:
        if not ha.drop_flow(flow_id):
            return _message("Invalid flow specified", 400)
        return _message("Flow aborted", 200)

    @router.get("/auth/authorize")
    async def authorize(request: Request) -> Response:
        q = request.query_params
        client_id, redirect_uri = q.get("client_id", ""), q.get("redirect_uri", "")
        if not verify_client_id(client_id) or not verify_redirect_uri(client_id, redirect_uri):
            return HTMLResponse("<p>This app cannot log in here: unknown client or redirect.</p>", status_code=400)
        page = AUTHORIZE_PAGE.replace("__PARAMS__", html.escape(json.dumps({
            "client_id": client_id, "redirect_uri": redirect_uri, "state": q.get("state"),
            "code_challenge": q.get("code_challenge"), "code_challenge_method": q.get("code_challenge_method"),
        }), quote=True))
        return HTMLResponse(page, headers={**_NO_STORE, "X-Frame-Options": "DENY",
                                           "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; "
                                                                      "style-src 'unsafe-inline'; frame-ancestors 'none'"})

    _register_ws(runtime)
    return router


def _register_ws(runtime: Any) -> None:
    from services.homehub import api as hub_api

    async def current_user(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
        p = conn.principal
        users = {u["id"]: u for u in runtime.store.get_kv("users", []) or []}
        user = users.get(getattr(p, "user_id", "") or "", {})
        await conn.result(mid, {
            "id": getattr(p, "user_id", None), "name": getattr(p, "name", None) or user.get("name"),
            "is_owner": bool(user.get("is_owner")), "is_admin": bool(getattr(p, "is_admin", False)),
            "credentials": [{"auth_provider_type": "homeassistant", "auth_provider_id": None}],
            "mfa_modules": [],
        })

    hub_api.WS_COMMANDS["auth/current_user"] = current_user


# A plain page: the app's web view posts the flow, then follows the redirect
# with the code. Inline script only (the CSP above allows nothing else), no
# third-party resources, and the parameters arrive as escaped JSON in a data
# attribute, never interpolated into script.
AUTHORIZE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log in</title>
<style>
:root{color-scheme:light dark}
body{font-family:system-ui,sans-serif;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;
background:Canvas;color:CanvasText}
form{width:min(22rem,90vw);display:flex;flex-direction:column;gap:.75rem}
input,button{font-size:1rem;padding:.7rem;border-radius:.5rem;border:1px solid GrayText}
button{cursor:pointer}
#err{color:#c62828;min-height:1.2em}
</style></head>
<body data-params="__PARAMS__">
<form id="f" autocomplete="on">
<h1 style="font-size:1.4rem;margin:0 0 .5rem">Log in to your home</h1>
<input id="u" name="username" autocomplete="username" placeholder="Username" required>
<input id="p" name="password" type="password" autocomplete="current-password" placeholder="Password" required>
<div id="err" role="alert"></div>
<button type="submit">Log in</button>
</form>
<script>
(function(){
var P=JSON.parse(document.body.getAttribute('data-params'));
var flow=null;
function start(){
  var b={client_id:P.client_id,handler:['homeassistant',null],redirect_uri:P.redirect_uri};
  if(P.code_challenge){b.code_challenge=P.code_challenge;b.code_challenge_method=P.code_challenge_method;}
  return fetch('/auth/login_flow',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})
    .then(function(r){return r.json();}).then(function(j){flow=j.flow_id;});
}
var ready=start();
document.getElementById('f').addEventListener('submit',function(e){
  e.preventDefault();
  var err=document.getElementById('err');err.textContent='';
  ready.then(function(){
    return fetch('/auth/login_flow/'+encodeURIComponent(flow),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({client_id:P.client_id,username:document.getElementById('u').value,
      password:document.getElementById('p').value})});
  }).then(function(r){return r.json();}).then(function(j){
    if(j.type==='create_entry'){
      var u=new URL(P.redirect_uri);u.searchParams.set('code',j.result);
      if(P.state){u.searchParams.set('state',P.state);}
      window.location.href=u.toString();return;
    }
    err.textContent=(j.errors&&j.errors.base)==='invalid_auth'?'Wrong username or password.':(j.message||'Could not log in.');
    if(j.type!=='form'){ready=start();}
  }).catch(function(){err.textContent='The hub could not be reached.';});
});
})();
</script>
</body></html>
"""
