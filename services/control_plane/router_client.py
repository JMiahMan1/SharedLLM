"""Read-only client for an OpenWrt router's ubus JSON-RPC API (docs/NETWORK.md).

Jarvis logs in as its own rpcd user whose ACL only allows reading status
(scripts/router_setup.sh creates it), so nothing here can change the router.

The router's HTTPS certificate is self-signed, so it cannot be verified by a
CA. Instead its SHA-256 fingerprint is pinned the first time Jarvis connects;
a router presenting any other certificate never receives the password.
"""
from __future__ import annotations

import hashlib
import socket
import ssl
import warnings
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

#: rpcd's "no session yet" id, used only for session.login.
_NO_SESSION = "0" * 32
#: ubus status codes: 0 is success, 6 is permission denied (also an expired session).
_UBUS_OK = 0
_UBUS_PERMISSION_DENIED = 6
#: rpcd answers an unknown or expired session at the JSON-RPC level.
_JSONRPC_ACCESS_DENIED = -32002


class RouterError(Exception):
    """The router could not be reached, refused the login, or a call failed."""


class CertificateChanged(RouterError):
    """The router presented a certificate other than the pinned one."""


def certificate_fingerprint(url: str, timeout: float = 10) -> str:
    """SHA-256 of the certificate the HTTPS server at ``url`` presents."""
    parsed = urlparse(url)
    host, port = parsed.hostname or "", parsed.port or 443
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True) or b""
    return hashlib.sha256(der).hexdigest()


class RouterClient:
    def __init__(self, url: str, username: str, password: str, pin: str | None = None, timeout: float = 15):
        self.url = url.rstrip("/")
        self.endpoint = self.url + "/ubus"
        self.username = username
        self.password = password
        self.pin = (pin or "").lower() or None
        self.timeout = timeout
        self.session: str | None = None
        self._id = 0

    def _rpc(self, session: str, obj: str, method: str, args: dict | None) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": "call", "params": [session, obj, method, args or {}]}
        try:
            # The certificate is checked by its pinned fingerprint instead.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                resp = requests.post(self.endpoint, json=body, timeout=self.timeout, verify=False)
        except requests.RequestException as e:
            raise RouterError(f"router unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise RouterError(f"router answered HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            code = (data.get("error") or {}).get("code")
            if code == _JSONRPC_ACCESS_DENIED:
                return _UBUS_PERMISSION_DENIED, None
            raise RouterError(f"ubus error {code}")
        result = data.get("result") or []
        status = result[0] if result else None
        return status, (result[1] if len(result) > 1 else None)

    def verify_certificate(self) -> str:
        """Check the router's certificate against the pin; returns its fingerprint.

        With no pin yet the fingerprint is returned for the caller to store.
        """
        if not self.url.startswith("https://"):
            raise RouterError("the router URL must use https")
        try:
            seen = certificate_fingerprint(self.url, self.timeout)
        except OSError as e:
            raise RouterError(f"router unreachable: {type(e).__name__}") from e
        if self.pin and seen != self.pin:
            raise CertificateChanged("the router's certificate changed since it was pinned")
        return seen

    def login(self) -> None:
        self.verify_certificate()
        status, data = self._rpc(_NO_SESSION, "session", "login",
                                 {"username": self.username, "password": self.password})
        if status != _UBUS_OK or not data or not data.get("ubus_rpc_session"):
            raise RouterError("the router refused the login")
        self.session = data["ubus_rpc_session"]

    def call(self, obj: str, method: str, args: dict | None = None) -> Any:
        """One ubus call; logs in, or again once if the session expired."""
        for attempt in (1, 2):
            if not self.session:
                self.login()
            status, data = self._rpc(self.session or _NO_SESSION, obj, method, args)
            if status == _UBUS_OK:
                return data if data is not None else {}
            if status == _UBUS_PERMISSION_DENIED and attempt == 1:
                self.session = None
                continue
            raise RouterError(f"{obj}.{method} failed (ubus status {status})")
        raise RouterError(f"{obj}.{method} was denied")
