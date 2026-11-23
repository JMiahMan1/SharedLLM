"""Automatic multi-service account provisioning when a Jarvis user is created.

Best-effort, non-fatal provisioning for HomeHub, Audiobookshelf, Music Assistant, and Nextcloud.
Never logs or returns passwords, API keys, or tokens.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlmodel import Session, select

from services import config
from services.identity.crypto import encrypt
from services.identity.models import User

log = logging.getLogger("identity.onboarding")

# Timeout for external HTTP calls
HTTP_TIMEOUT_S = 10.0


def _should_skip_onboarding(user: User) -> bool:
    """Check if onboarding should be skipped for this user."""
    return bool(
        user.is_system_default
        or user.id == 1
        or user.username == "default"
        or getattr(user, "is_test_account", False)
    )


def _http_post_json(url: str, headers: dict[str, str], data: dict[str, Any], timeout: float = HTTP_TIMEOUT_S) -> dict[str, Any]:
    """POST JSON to a URL and return parsed JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Request failed: {type(exc).__name__}") from None


def _http_get_json(url: str, headers: dict[str, str], timeout: float = HTTP_TIMEOUT_S) -> dict[str, Any]:
    """GET JSON from a URL and return parsed JSON response."""
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Request failed: {type(exc).__name__}") from None


def _http_post_form(url: str, headers: dict[str, str], data: dict[str, str], timeout: float = HTTP_TIMEOUT_S) -> dict[str, Any]:
    """POST form-encoded data to a URL and return parsed JSON response."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read()
            return json.loads(resp_body or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Request failed: {type(exc).__name__}") from None


def _provision_homehub(user: User, raw_password: str | None) -> dict[str, Any]:
    """Provision HomeHub user account."""
    hub_url = (config.HOMEHUB_URL or "").strip().rstrip("/")
    secret = config.INTERNAL_SECRET or ""

    if not hub_url:
        return {"status": "skipped", "detail": "HOMEHUB_URL not configured"}

    if not secret:
        return {"status": "error", "detail": "INTERNAL_SECRET not configured"}

    try:
        resp = _http_post_json(
            f"{hub_url}/hub/auth/users",
            headers={"X-Internal-Secret": secret},
            data={
                "username": user.username,
                "display_name": user.display_name or user.username,
                "password": raw_password,
                "is_admin": bool(user.is_admin),
            },
        )
        status = "created" if resp.get("created") else "existing"
        return {"status": status, "detail": resp}
    except RuntimeError as exc:
        log.warning(f"[onboarding] HomeHub provisioning failed for {user.username}: {exc}")
        return {"status": "error", "detail": str(exc)}


def _find_admin_abs_credentials(session: Session, admin: User | None) -> tuple[str | None, str | None]:
    """Find admin Audiobookshelf URL and API key."""
    # Try the provided admin user first
    if admin and admin.audiobookshelf_url and admin.audiobookshelf_api_key_enc:
        from services.identity.crypto import decrypt
        url = admin.audiobookshelf_url
        key = decrypt(admin.audiobookshelf_api_key_enc)
        if url and key:
            return url, key

    # Query for any admin user with both
    for candidate in session.exec(select(User).where(User.is_admin == True)).all():  # noqa: E712
        if candidate.audiobookshelf_url and candidate.audiobookshelf_api_key_enc:
            from services.identity.crypto import decrypt
            url = candidate.audiobookshelf_url
            key = decrypt(candidate.audiobookshelf_api_key_enc)
            if url and key:
                return url, key

    return None, None


def _provision_audiobookshelf(session: Session, user: User, admin: User | None, raw_password: str | None) -> dict[str, Any]:
    """Provision Audiobookshelf user account."""
    # Skip if user already has Audiobookshelf API key
    if user.audiobookshelf_api_key_enc:
        return {"status": "existing", "detail": "User already has Audiobookshelf credentials"}

    abs_url, admin_key = _find_admin_abs_credentials(session, admin)
    if not abs_url or not admin_key:
        return {"status": "skipped", "detail": "Audiobookshelf admin credentials not configured"}

    abs_url = abs_url.rstrip("/")
    headers = {"Authorization": f"Bearer {admin_key}"}

    try:
        # Check for existing user
        users_resp = _http_get_json(f"{abs_url}/api/users", headers=headers)
        existing_user = None
        for u in users_resp.get("users", []):
            if u.get("username", "").lower() == user.username.lower():
                existing_user = u
                break

        if existing_user:
            # Update local credentials even for existing users
            user.audiobookshelf_url = abs_url
            user.audiobookshelf_user = existing_user.get("username") or user.username
            token = existing_user.get("token") or existing_user.get("apiKey")
            if token:
                user.audiobookshelf_api_key_enc = encrypt(str(token))
            session.add(user)
            session.commit()
            session.refresh(user)
            return {"status": "existing", "detail": "User already exists in Audiobookshelf, credentials updated"}

        # Create new user
        password = raw_password or secrets.token_urlsafe(18)
        permissions = {
            "download": True,
            "update": bool(user.is_admin),
            "delete": bool(user.is_admin),
            "upload": bool(user.is_admin),
            "accessAllLibraries": True,
            "accessAllTags": True,
            "accessExplicitContent": user.family_role not in ("child", "teen"),
        }
        create_resp = _http_post_json(
            f"{abs_url}/api/users",
            headers=headers,
            data={
                "username": user.username,
                "password": password,
                "type": "admin" if user.is_admin else "user",
                "isActive": True,
                "permissions": permissions,
            },
        )

        abs_user = create_resp.get("user") or create_resp
        token = abs_user.get("token") or abs_user.get("apiKey")

        # Persist user's Audiobookshelf credentials
        user.audiobookshelf_url = abs_url
        user.audiobookshelf_user = abs_user.get("username") or user.username
        if token:
            user.audiobookshelf_api_key_enc = encrypt(str(token))
        session.add(user)
        session.commit()
        session.refresh(user)

        return {"status": "created", "detail": "Audiobookshelf account created"}

    except RuntimeError as exc:
        log.warning(f"[onboarding] Audiobookshelf provisioning failed for {user.username}: {exc}")
        return {"status": "error", "detail": str(exc)}


def _provision_music_assistant(
    session: Session,
    user: User,
    resolve_ma_fn: Any,
    sync_ma_abs_fn: Any,
) -> dict[str, Any]:
    """Provision Music Assistant account and sync Audiobookshelf provider."""
    try:
        # Call resolve_ma_fn which provisions MA account + token if household MA is configured
        res = resolve_ma_fn(session, user)
        if res is not None and getattr(res, "error", None) and not getattr(res, "token", None):
            err_str = str(res.error).lower()
            if "not configured" in err_str or "not set up" in err_str or "no music assistant" in err_str:
                return {"status": "skipped", "detail": str(res.error)}
            return {"status": "error", "detail": str(res.error)}

        # Call sync_ma_abs_fn so the user's MA provider_filter is immediately bound
        # to ONLY their own ABS provider (or shared music only if ABS wasn't configured)
        sync_ma_abs_fn(session, user)

        status = "created" if getattr(res, "login", None) == "provisioned" else "existing"
        return {"status": status, "detail": "Music Assistant account ready and ABS provider scoped"}
    except Exception as exc:
        log.warning(f"[onboarding] Music Assistant provisioning failed for {user.username}: {exc}")
        err_str = str(exc).lower()
        if "not configured" in err_str or "not set up" in err_str or "no music assistant" in err_str:
            return {"status": "skipped", "detail": "Music Assistant not configured"}
        return {"status": "error", "detail": str(exc)}


def _find_admin_nextcloud_credentials(session: Session, admin: User | None) -> tuple[str | None, str | None, str | None]:
    """Find admin Nextcloud URL, username, and password."""
    # Try the provided admin user first
    if admin and admin.nextcloud_url and admin.nextcloud_user and admin.nextcloud_pass_enc:
        from services.identity.crypto import decrypt
        url = admin.nextcloud_url
        username = admin.nextcloud_user
        password = decrypt(admin.nextcloud_pass_enc)
        if url and username and password:
            return url, username, password

    # Query for any admin user with all three
    for candidate in session.exec(select(User).where(User.is_admin == True)).all():  # noqa: E712
        if candidate.nextcloud_url and candidate.nextcloud_user and candidate.nextcloud_pass_enc:
            from services.identity.crypto import decrypt
            url = candidate.nextcloud_url
            username = candidate.nextcloud_user
            password = decrypt(candidate.nextcloud_pass_enc)
            if url and username and password:
                return url, username, password

    return None, None, None


def _provision_nextcloud(session: Session, user: User, admin: User | None, raw_password: str | None) -> dict[str, Any]:
    """Provision Nextcloud user account and app password."""
    if user.nextcloud_pass_enc:
        return {"status": "existing", "detail": "User already has Nextcloud credentials"}
    nc_url, nc_user, nc_pass = _find_admin_nextcloud_credentials(session, admin)
    if not nc_url or not nc_user or not nc_pass:
        return {"status": "skipped", "detail": "Nextcloud admin credentials not configured"}

    nc_url = nc_url.rstrip("/")
    auth_header = base64.b64encode(f"{nc_user}:{nc_pass}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }

    try:
        # Create user (idempotent: 100=created, 102=already exists)
        password = raw_password or secrets.token_urlsafe(18)
        display_name = user.display_name or user.username
        create_resp = _http_post_form(
            f"{nc_url}/ocs/v1.php/cloud/users",
            headers=headers,
            data={
                "userid": user.username,
                "password": password,
                "displayName": display_name,
            },
        )

        ocs_status = create_resp.get("ocs", {}).get("meta", {}).get("statuscode")
        if ocs_status not in (100, 102):
            return {"status": "error", "detail": f"Nextcloud user creation returned statuscode {ocs_status}"}

        user_created = ocs_status == 100
        status = "created" if user_created else "existing"

        # Create app password (always create a new one for Jarvis)
        app_pass_resp = _http_post_form(
            f"{nc_url}/ocs/v1.php/cloud/users/{user.username}/app-passwords",
            headers=headers,
            data={"name": "jarvis-onboarding"},
        )

        app_password = app_pass_resp.get("ocs", {}).get("data", {}).get("password")
        if not app_password:
            return {"status": "error", "detail": "Nextcloud did not return app password"}

        # Persist user's Nextcloud credentials
        user.nextcloud_url = nc_url
        user.nextcloud_user = user.username
        user.nextcloud_pass_enc = encrypt(app_password)
        session.add(user)
        session.commit()
        session.refresh(user)

        return {"status": status, "detail": f"Nextcloud user {'created' if user_created else 'existing'}, app password created"}

    except RuntimeError as exc:
        log.warning(f"[onboarding] Nextcloud provisioning failed for {user.username}: {exc}")
        return {"status": "error", "detail": str(exc)}


def provision_new_user_accounts(
    session: Session,
    user: User,
    *,
    admin: User | None = None,
    raw_password: str | None = None,
    sync_ma_abs_fn: Any = None,
    resolve_ma_fn: Any = None,
) -> dict[str, dict[str, Any]]:
    """
    Provision accounts for a new user across multiple services.

    Args:
        session: Database session
        user: The user to provision accounts for
        admin: Admin user to use for provisioning (preferred for credentials)
        raw_password: The user's plaintext password (if available)
        sync_ma_abs_fn: Function to sync MA ABS provider (session, user)
        resolve_ma_fn: Function to resolve/provision MA account (session, user)

    Returns:
        Dict mapping service name to provisioning result with 'status' and 'detail'.
        Status is one of: 'created', 'existing', 'skipped', 'error'.
    """
    if _should_skip_onboarding(user):
        return {}

    results: dict[str, dict[str, Any]] = {}

    # HomeHub
    results["homehub"] = _provision_homehub(user, raw_password)

    # Audiobookshelf
    results["audiobookshelf"] = _provision_audiobookshelf(session, user, admin, raw_password)

    # Music Assistant
    if resolve_ma_fn and sync_ma_abs_fn:
        results["music_assistant"] = _provision_music_assistant(session, user, resolve_ma_fn, sync_ma_abs_fn)
    else:
        results["music_assistant"] = {"status": "skipped", "detail": "resolve_ma_fn or sync_ma_abs_fn not provided"}

    # Nextcloud
    results["nextcloud"] = _provision_nextcloud(session, user, admin, raw_password)

    return results
