"""The ESPHome Device Builder (dashboard) as an integration: where it is, and whether it answers.

HA ran the builder as an add-on and linked it from its sidebar. Here it is its
own container (the ``esphome`` service in docker-compose.yml) and this entry
records where admins reach it: ``entry.data`` holds ``url`` and, when the
dashboard has a login, ``username``/``password``. Nothing about it is a
constant: the URL and the login are whatever the admin entered, and the
Devices page shows the link only while such an entry exists.

There are no entities. The entry is ``loaded`` while the dashboard answers and
``setup_retry`` (with the reason) while it does not.
"""
from __future__ import annotations

from typing import Any

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, HubError

TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)


async def check_dashboard(url: str, username: str | None = None, password: str | None = None) -> dict[str, Any]:
    """GET the dashboard; ``{"status", "login"}``. Raises DeviceUnavailable when nothing answers.

    A 401 means the dashboard is there and wants a login; with the right login
    it answers 200 (or redirects to its login page, which aiohttp follows).
    """
    auth = aiohttp.BasicAuth(username, password or "") if username else None
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session, session.get(url, auth=auth) as resp:
            return {"status": resp.status, "login": resp.status == 401}
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise DeviceUnavailable(f"the ESPHome builder at {url} did not answer: {exc or type(exc).__name__}") from exc


class ESPHomeBuilderDriver(Driver):
    platform = "esphome_builder"
    poll_interval = 300.0
    check = staticmethod(check_dashboard)

    async def async_setup(self) -> None:
        url = str(self.entry.data.get("url") or "")
        if not url:
            raise HubError("esphome_builder entry has no url")
        result = await self.check(url, self.entry.data.get("username"), self.entry.data.get("password"))
        if result["status"] >= 500:
            raise DeviceUnavailable(f"the ESPHome builder answered HTTP {result['status']}")

    async def async_poll(self) -> None:
        try:
            await self.async_setup()
        except HubError as exc:
            self.entry.state, self.entry.reason = "setup_retry", str(exc)
        else:
            self.entry.state, self.entry.reason = "loaded", None
