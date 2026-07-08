import re
import urllib.parse
import logging
from typing import Any, Optional

from services.config import NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS
from .http_client import request

log = logging.getLogger("execution.nextcloud")


def resolve_credentials(user_context: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    return (
        getattr(user_context, "nextcloud_url", None) or NEXTCLOUD_URL,
        getattr(user_context, "nextcloud_user", None) or NEXTCLOUD_USER,
        getattr(user_context, "nextcloud_pass", None) or NEXTCLOUD_PASS,
    )


def ocs_headers() -> dict[str, str]:
    return {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or fallback


def webdav_url(base_url: str, username: str, path: str = "") -> str:
    base = f"{base_url.rstrip('/')}/remote.php/dav/files/{username}"
    if not path:
        return base
    encoded = "/".join(urllib.parse.quote(part) for part in path.split("/") if part)
    return f"{base}/{encoded}"


def ocs_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}{endpoint}"


async def ocs_request(
    method: str,
    base_url: str,
    username: str,
    password: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[bool, Any, str]:
    request_params = {"format": "json"}
    if params:
        request_params.update(params)

    resp = await request(
        method,
        ocs_url(base_url, endpoint),
        auth=(username, password),
        headers=ocs_headers(),
        params=request_params,
        data=data,
        timeout=timeout,
        verify=False,
    )

    try:
        payload = __import__("json").loads(resp["text"])
    except ValueError:
        text = (resp["text"] or "").strip()
        return resp["ok"], None, text[:400]

    if isinstance(payload, dict) and "ocs" in payload:
        meta = payload.get("ocs", {}).get("meta", {}) or {}
        data = payload.get("ocs", {}).get("data")
        ok = str(meta.get("status", "")).lower() == "ok" or int(meta.get("statuscode", 0) or 0) in {100, 200}
        message = str(meta.get("message", "") or "")
        return ok and resp["ok"], data, message

    return resp["ok"], payload, ""


async def ensure_webdav_dir(base_url: str, username: str, password: str, path: str) -> None:
    folder_url = webdav_url(base_url, username, path)
    resp = await request(
        "PROPFIND", folder_url,
        auth=(username, password),
        timeout=30,
        verify=False,
    )
    if resp["status_code"] == 404:
        await request(
            "MKCOL", folder_url,
            auth=(username, password),
            timeout=30,
            verify=False,
        )
