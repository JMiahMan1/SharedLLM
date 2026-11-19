"""The hub's own users and passwords (``local``): always there, so a hub with nothing else still works.

Its users are the ones imported from Home Assistant and the ones an admin adds on the hub; their passwords
are bcrypt hashes in the sealed ``ha_auth`` blob (:mod:`services.homehub.ha_auth`), checked there.
"""
from __future__ import annotations

import asyncio
from typing import Any

from services.homehub.auth_providers.base import AuthProvider, LoginRejected


class LocalProvider(AuthProvider):
    type = "local"
    label = "This hub"
    external = False

    def __init__(self, ha_auth: Any) -> None:
        self.ha_auth = ha_auth

    async def list_users(self) -> list[Any]:
        return []  # the hub's own users are the hub's users; nothing to mirror

    async def verify_login(self, username: str, password: str) -> str:
        cred = await asyncio.to_thread(self.ha_auth.check_password, username, password)
        if cred is None:
            raise LoginRejected("Wrong username or password")
        return str(cred["user_id"])
