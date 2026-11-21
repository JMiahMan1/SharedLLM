"""Sign everyone out and reset their keys; the automated agents' test account.

Built for the day a key leaks. ``POST /api/users/security/sign-in-reset`` (admin only,
``{"confirm": "RESET"}``, optionally ``"username"`` for one person):

* every person's API key (``User.api_key_*``) is replaced by a new random one, which only a
  password sign-in hands out, so every phone, browser and the Android app must sign in again;
* every extra key a person minted (``APIKey`` rows: ESPHome Jarvis satellites are
  ``device:<name>``, anything else a client someone set up by hand) is deleted;
* every Windows PC agent key (``Device.agent_key_hash``) is cleared;
* for everyone, the home hub revokes every person's hub login too (companion apps and
  people's long-lived tokens; services' tokens stay and are listed);
* each revoked key's lookup hash is kept in ``RevokedCredential``, so a device that presents
  one is told "Your sign-in was reset, please sign in again" (401, ``code: sign_in_reset``)
  and its app shows the sign-in screen instead of failing silently.

What cannot be re-issued automatically is reported, by kind, with how to re-enrol it, and so
are the third-party credentials a leak of the identity database would have exposed (they can
only be rotated at their provider). The report holds no secret and is kept as the setting
``security_last_sign_in_reset`` so the admin still sees it after signing back in.

``POST /api/users/security/test-account`` creates (or re-keys) the account automated agents
use: no admin, no parent role, no roles, no password, and left out of the household roster.
Its key is returned once; ``scripts/create_test_account.py`` writes it straight to a file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from services import config
from services.identity.crypto import digest_candidates
from services.identity.models import APIKey, Device, GlobalSetting, LinkedAccount, RevokedCredential, User

log = logging.getLogger("identity.sign_in_reset")

SIGN_IN_RESET_CODE = "sign_in_reset"
SIGN_IN_RESET_MESSAGE = "Your sign-in was reset, please sign in again."
CONFIRM_WORD = "RESET"
LAST_RESET_SETTING = "security_last_sign_in_reset"
DEFAULT_TEST_USERNAME = "test-agent"
_TEST_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
#: How long the hub may take to sign everyone out.
HUB_TIMEOUT_S = 10.0

#: What each re-enrolment kind needs, said once so the API and the docs agree.
HOW = {
    "windows_guard": "Re-run agent/jarvis-guard/install.ps1 on that PC as a parent (it mints a new device key).",
    "esphome_satellite": "Pair it again: Devices > Add a device, then pick the satellite (it gets a new key).",
    "api_key": "Create a new key in Settings > API keys and put it into the client that used the old one.",
    "homehub_companion_app": "Open the Home Assistant companion app and sign in again.",
    "homehub_long_lived_token": "Service tokens were kept. If one may have been exposed, revoke and re-issue it in the hub.",
}


#: Identity's own refusal of a presented key (unknown or revoked some other way). The apps sign
#: out only on this code or ``sign_in_reset``, never on any other 401.
API_KEY_INVALID_CODE = "api_key_invalid"
API_KEY_INVALID_MESSAGE = "Invalid API Key"


class SignInWasReset(Exception):
    """Raised by an auth check when the presented key was revoked by a sign-in reset."""


class ApiKeyRefused(Exception):
    """Raised by an auth check when the presented key is not any account's key."""


def install_handler(app: FastAPI) -> None:
    """401 ``{"detail": ..., "code": "sign_in_reset"}``: the apps clear the key and say why."""

    @app.exception_handler(SignInWasReset)
    async def _signed_out(_request: Request, _exc: SignInWasReset) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": SIGN_IN_RESET_MESSAGE, "code": SIGN_IN_RESET_CODE},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    @app.exception_handler(ApiKeyRefused)
    async def _refused(_request: Request, _exc: ApiKeyRefused) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": API_KEY_INVALID_MESSAGE, "code": API_KEY_INVALID_CODE},
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )


def refusal(session: Session, key: str | None) -> Exception:
    """The 401 for a presented key no account holds: the reset's, or the plain refusal."""
    return SignInWasReset() if was_reset(session, key) else ApiKeyRefused()


def was_reset(session: Session, key: str | None) -> bool:
    """Was this key revoked by a sign-in reset? (Only its hash is compared.)"""
    candidates = digest_candidates((key or "").strip())
    if not candidates:
        return False
    row = session.exec(
        select(RevokedCredential).where(RevokedCredential.key_hash.in_(candidates))  # type: ignore[attr-defined]
    ).first()
    return row is not None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _revoke(session: Session, key_hash: str | None, kind: str, username: str | None) -> None:
    if key_hash:
        session.add(RevokedCredential(key_hash=key_hash, kind=kind, username=username))


def _label_of(field: str) -> str:
    return field.removesuffix("_enc").replace("_", " ")


def third_party_credentials(session: Session) -> list[dict[str, Any]]:
    """Credentials for other services held (encrypted) here, counted per kind, never shown.

    A leak of the database and its key exposes these; only their provider can replace them.
    """
    users = session.exec(select(User)).all()
    fields = [f for f in User.model_fields if f.endswith("_enc") and f != "api_key_enc"]
    out = []
    for field in fields:
        count = sum(1 for u in users if getattr(u, field, None))
        if count:
            out.append({"field": field.removesuffix("_enc"), "label": _label_of(field), "accounts": count})
    linked = session.exec(select(LinkedAccount)).all()
    providers: dict[str, int] = {}
    for row in linked:
        name = str(getattr(row, "provider", "") or "linked account")
        providers[name] = providers.get(name, 0) + 1
    for name, count in sorted(providers.items()):
        out.append({"field": f"linked:{name}", "label": f"{name} link (OAuth tokens)", "accounts": count})
    settings = session.exec(select(GlobalSetting)).all()
    shared = [s.key for s in settings if s.value and (s.key.endswith("_enc") or s.key.endswith("_secret")
                                                       or s.key.endswith("_token"))]
    for key in sorted(shared):
        out.append({"field": f"setting:{key}", "label": f"household setting {_label_of(key)}", "accounts": 1})
    return out


def _devices_with_agent_keys(session: Session, usernames: set[str] | None) -> list[Device]:
    rows = session.exec(select(Device).where(Device.agent_key_hash != "")).all()
    return [d for d in rows if usernames is None or (d.owner_username or "") in usernames]


def _re_enrol_items(session: Session, users: list[User], usernames: set[str] | None,
                    everyone: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for device in _devices_with_agent_keys(session, usernames):
        items.append({"kind": "windows_guard", "label": device.label or device.device_key,
                      "owner": device.owner_username, "how": HOW["windows_guard"]})
    ids = {u.id for u in users}
    names = {u.id: u.username for u in users}
    for key in session.exec(select(APIKey)).all():
        if key.user_id not in ids:
            continue
        label = key.label or "API key"
        kind = "esphome_satellite" if label.startswith("device:") else "api_key"
        items.append({"kind": kind, "label": label.removeprefix("device:") if kind == "esphome_satellite" else label,
                      "owner": names.get(key.user_id), "how": HOW[kind]})
    if everyone:
        items.append({"kind": "homehub_companion_app", "label": "Home Assistant companion apps", "owner": None,
                      "how": HOW["homehub_companion_app"]})
    return items


def preview(session: Session) -> dict[str, Any]:
    users = session.exec(select(User)).all()
    return {
        "users": len(users),
        "api_keys": len(session.exec(select(APIKey)).all()),
        "device_agent_keys": len(_devices_with_agent_keys(session, None)),
        "re_enrol": _re_enrol_items(session, list(users), None, True),
        "third_party": third_party_credentials(session),
        "last_reset": last_reset(session),
    }


def last_reset(session: Session) -> dict[str, Any] | None:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == LAST_RESET_SETTING)).first()
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except ValueError:
        log.error("the stored sign-in reset report is unreadable")
        return None
    return data if isinstance(data, dict) else None


def _save_report(session: Session, report: dict[str, Any]) -> None:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == LAST_RESET_SETTING)).first()
    value = json.dumps(report)
    if row is None:
        session.add(GlobalSetting(key=LAST_RESET_SETTING, value=value,
                                  description="The last sign-in reset's report (no secrets)."))
    else:
        row.value = value
        session.add(row)


HubSignOut = Callable[[], dict[str, Any]]


def hub_sign_out_from_env() -> dict[str, Any]:
    """Ask the home hub to revoke every person's login. Skipped (and said so) when no hub is set."""
    # Both resolved once, when the service started (services/config.py).
    url = (config.HOMEHUB_URL or "").strip().rstrip("/")
    secret = config.INTERNAL_SECRET or ""
    if not url:
        return {"status": "skipped", "revoked": 0,
                "message": "No home hub is configured for identity (HOMEHUB_URL). If the hub is in use, "
                           "run scripts/reset_sign_ins.sh --hub-only on the server."}
    req = urllib.request.Request(f"{url}/hub/auth/sign-out-everyone", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Internal-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=HUB_TIMEOUT_S) as resp:
            body = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": "failed", "revoked": 0,
                "message": f"The home hub could not be reached ({type(exc).__name__}). Its logins are still "
                           "valid: run scripts/reset_sign_ins.sh --hub-only on the server."}
    revoked = int(body.get("companion_logins_revoked") or 0) + int(body.get("people_long_lived_revoked") or 0)
    kept = [str(k) for k in body.get("service_tokens_kept") or []]
    message = f"The hub signed out {revoked} login(s)."
    if kept:
        message += f" Service tokens kept: {', '.join(kept)}."
    return {"status": "revoked", "revoked": revoked, "message": message, "service_tokens_kept": kept}


def reset(
    session: Session,
    *,
    actor: User,
    store_user_api_key: Callable[[User, str], Any],
    username: str | None = None,
    hub_sign_out: HubSignOut | None = None,
) -> dict[str, Any]:
    """Revoke and regenerate keys; returns the report (no secrets). Commits."""
    everyone = username is None
    if everyone:
        users = list(session.exec(select(User)).all())
    else:
        target = session.exec(select(User).where(User.username == (username or "").strip().lower())).first()
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        users = [target]
    scope_names = None if everyone else {u.username for u in users}
    re_enrol = _re_enrol_items(session, users, scope_names, everyone)

    ids = {u.id for u in users}
    keys_revoked = 0
    for key in session.exec(select(APIKey)).all():
        if key.user_id in ids:
            owner = next((u.username for u in users if u.id == key.user_id), None)
            _revoke(session, key.key_hash, "api_key", owner)
            session.delete(key)
            keys_revoked += 1

    agents_revoked = 0
    for device in _devices_with_agent_keys(session, scope_names):
        _revoke(session, device.agent_key_hash, "agent_key", device.owner_username)
        device.agent_key_hash = ""
        device.agent_key_created_at = None
        session.add(device)
        agents_revoked += 1

    for user in users:
        _revoke(session, user.api_key_hash, "user_api_key", user.username)
        store_user_api_key(user, os.urandom(24).hex())
        session.add(user)

    if everyone:
        hub = (hub_sign_out or hub_sign_out_from_env)()
    else:
        hub = {"status": "skipped", "revoked": 0,
               "message": "Hub logins are only reset when everyone is signed out."}
    if everyone and hub.get("service_tokens_kept"):
        re_enrol.append({"kind": "homehub_long_lived_token", "label": ", ".join(hub["service_tokens_kept"]),
                         "owner": None, "how": HOW["homehub_long_lived_token"]})

    report = {
        "status": "SUCCESS",
        "scope": "everyone" if everyone else "user",
        "username": None if everyone else users[0].username,
        "at": _now(),
        "by": actor.username,
        "users_reset": len(users),
        "api_keys_revoked": keys_revoked,
        "device_agent_keys_revoked": agents_revoked,
        "homehub": {k: hub.get(k) for k in ("status", "revoked", "message")},
        "re_enrol": re_enrol,
        "third_party": third_party_credentials(session) if everyone else [],
        "caller_signed_out": any(u.id == actor.id for u in users),
    }
    _save_report(session, report)
    session.commit()
    log.warning("sign-in reset by %s: %s user(s), %d extra key(s), %d PC agent key(s); hub %s",
                actor.username, report["users_reset"], keys_revoked, agents_revoked, hub.get("status"))
    return report


def create_or_rekey_test_account(
    session: Session,
    *,
    store_user_api_key: Callable[[User, str], Any],
    username: str | None,
    display_name: str | None,
) -> tuple[User, str, bool]:
    """The test account and a new key for it (returned once). Refuses to convert a real person."""
    name = (username or DEFAULT_TEST_USERNAME).strip().lower()
    if not _TEST_USERNAME.match(name):
        raise HTTPException(status_code=422, detail="username must be 3-32 lowercase letters, digits, - or _")
    user = session.exec(select(User).where(User.username == name)).first()
    created = user is None
    if user is None:
        user = User(username=name, display_name=(display_name or "Test agent").strip() or "Test agent",
                    is_test_account=True, is_admin=False, is_system_default=False, family_role="other")
        session.add(user)
        session.flush()
    elif not user.is_test_account:
        raise HTTPException(status_code=409, detail=f"{name} is a household account, not a test account. "
                                                    "Choose another username.")
    else:
        _revoke(session, user.api_key_hash, "user_api_key", user.username)
    # Least privilege, re-asserted every time: an earlier edit can never have widened it.
    user.is_admin = False
    user.is_system_default = False
    user.family_role = "other"
    user.mother_username = None
    user.password_hash = None
    key = os.urandom(24).hex()
    store_user_api_key(user, key)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user, key, created


def refuse_test_account_privilege(user: User, *, is_admin: Any = None, is_system_default: Any = None,
                                  family_role: str | None = None, role_ids: Any = None) -> None:
    """A test account is never an admin, the system account, a parent, or a role holder."""
    if not getattr(user, "is_test_account", False):
        return
    if is_admin or is_system_default:
        raise HTTPException(status_code=422, detail="A test account cannot be an admin or the system account")
    if family_role not in (None, "other"):
        raise HTTPException(status_code=422, detail="A test account has no family role")
    if role_ids:
        raise HTTPException(status_code=422, detail="A test account holds no roles")


def build_router(
    get_session: Callable[..., Any],
    require_admin: Callable[..., Any],
    store_user_api_key: Callable[[User, str], Any],
    audit: Callable[..., None],
) -> APIRouter:
    router = APIRouter()
    router.hub_sign_out = None  # type: ignore[attr-defined]  # tests replace the hub call here

    @router.get("/api/users/security/sign-in-reset")
    def get_preview(session: Session = Depends(get_session), _admin: User = Depends(require_admin)) -> dict:
        return preview(session)

    @router.get("/api/users/security/sign-in-reset/last")
    def get_last(session: Session = Depends(get_session), _admin: User = Depends(require_admin)) -> Any:
        return last_reset(session)

    @router.post("/api/users/security/sign-in-reset")
    def post_reset(
        request: Request,
        body: dict = Body(default_factory=dict),
        session: Session = Depends(get_session),
        admin: User = Depends(require_admin),
    ) -> dict:
        if str(body.get("confirm") or "") != CONFIRM_WORD:
            raise HTTPException(status_code=422, detail=f'Type {CONFIRM_WORD} to confirm: send {{"confirm": "{CONFIRM_WORD}"}}')
        username = body.get("username")
        if username is not None and not str(username).strip():
            raise HTTPException(status_code=422, detail="username must not be blank")
        report = reset(session, actor=admin, store_user_api_key=store_user_api_key,
                       username=str(username) if username is not None else None,
                       hub_sign_out=router.hub_sign_out)  # type: ignore[attr-defined]
        audit(request, session, "security.sign_in_reset", report["username"] or "everyone", actor=admin,
              changes=[{"field": "api_key", "change": "changed", "secret": True}],
              note=(f"{report['users_reset']} user(s), {report['api_keys_revoked']} extra key(s), "
                    f"{report['device_agent_keys_revoked']} PC agent key(s); hub {report['homehub']['status']}"))
        return report

    @router.post("/api/users/security/test-account")
    def post_test_account(
        request: Request,
        body: dict = Body(default_factory=dict),
        session: Session = Depends(get_session),
        admin: User = Depends(require_admin),
    ) -> dict:
        user, key, created = create_or_rekey_test_account(
            session, store_user_api_key=store_user_api_key,
            username=body.get("username"), display_name=body.get("display_name"),
        )
        audit(request, session, "security.test_account", user.username, actor=admin,
              changes=[{"field": "api_key", "change": "set" if created else "changed", "secret": True}],
              note="created" if created else "key replaced")
        return {"status": "SUCCESS", "username": user.username, "created": created, "api_key": key}

    return router


def cli(argv: list[str] | None = None) -> int:
    """The same reset from the server's console, inside the identity container
    (``scripts/reset_sign_ins.sh``). Prints the report (no secrets) as JSON."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m services.identity.sign_in_reset",
                                     description="Sign everyone (or one person) out and reset their keys.")
    parser.add_argument("--preview", action="store_true", help="only show what a reset would do")
    parser.add_argument("--user", help="reset one person instead of everyone")
    parser.add_argument("--confirm", default="", help=f"must be {CONFIRM_WORD} to reset")
    args = parser.parse_args(argv)

    from services.identity import main as identity

    with Session(identity.engine) as session:
        if args.preview:
            print(json.dumps(preview(session), indent=2))
            return 0
        if args.confirm != CONFIRM_WORD:
            print(f"refusing: pass --confirm {CONFIRM_WORD}", file=sys.stderr)
            return 2
        console = User(username="server-console", display_name="Server console")
        report = reset(session, actor=console, store_user_api_key=identity._store_user_api_key,
                       username=args.user)
        identity._audit(None, session, "security.sign_in_reset", report["username"] or "everyone", actor=console,
                        changes=[{"field": "api_key", "change": "changed", "secret": True}],
                        note="from the server console")
    print(json.dumps(report, indent=2))
    return 0 if report["homehub"]["status"] != "failed" else 3


if __name__ == "__main__":
    raise SystemExit(cli())
