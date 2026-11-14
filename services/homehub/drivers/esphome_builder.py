"""The ESPHome Device Builder (dashboard) as an integration: where it is, whether it answers, and firmware.

HA ran the builder as an add-on and linked it from its sidebar. Here it is its
own container (the ``esphome`` service in docker-compose.yml) and this entry
records where admins reach it: ``entry.data`` holds ``url`` and, when the
dashboard has a login, ``username``/``password``. Nothing about it is a
constant: the URL and the login are whatever the admin entered, and the
Devices page shows the link only while such an entry exists.

There are no entities of its own. The entry is ``loaded`` while the dashboard
answers and ``setup_retry`` (with the reason) while it does not.

**Firmware updates.** HA's ESPHome integration gives every node a ``Firmware``
update entity while the dashboard is linked: installed = the ESPHome version
the node runs, latest = the builder's own ESPHome version, and install =
compile the node's config, then flash it over the air. This driver does the
builder's half the same way, through the dashboard's own API: ``GET /devices``
lists the configured nodes (``name``, ``configuration``, ``current_version``)
and the ``/compile`` and ``/upload`` websockets run a job (``{"type": "spawn",
"configuration": ..., "port": "OTA"}``) and answer ``{"event": "line"}`` lines
and one ``{"event": "exit", "code": n}``. The node's own driver (``esphome.py``)
publishes the update entity from :meth:`ESPHomeBuilderDriver.firmware_for` and
calls :meth:`ESPHomeBuilderDriver.install`. A node the builder has no config for
is never offered an install.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, HubError

TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
#: A first compile downloads a toolchain and can take many minutes; an OTA a minute or two.
JOB_TIMEOUT = aiohttp.ClientTimeout(total=3600, connect=8)
#: Where the builder's release notes are (HA's ESPHome update entity links the same page).
RELEASE_URL = "https://esphome.io/changelog/"
#: Lines of a failed job's output kept for the log.
TAIL_LINES = 20


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


async def list_nodes(url: str, username: str | None = None, password: str | None = None) -> list[dict[str, Any]]:
    """The builder's configured nodes (``GET /devices``, the dashboard's own list)."""
    auth = aiohttp.BasicAuth(username, password or "") if username else None
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session, \
                session.get(urljoin(url, "devices"), auth=auth) as resp:
            if resp.status != 200:
                raise DeviceUnavailable(f"the ESPHome builder's device list answered HTTP {resp.status}")
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        raise DeviceUnavailable(f"the ESPHome builder's device list did not answer: {exc or type(exc).__name__}") \
            from exc
    configured = body.get("configured") if isinstance(body, dict) else None
    if not isinstance(configured, list):
        raise DeviceUnavailable("the ESPHome builder's device list has no configured nodes")
    return [n for n in configured if isinstance(n, dict) and n.get("name") and n.get("configuration")]


async def run_job(url: str, username: str | None, password: str | None, job: str, configuration: str,
                  port: str | None = None) -> tuple[bool, list[str]]:
    """Run ``compile`` or ``upload`` for one config; ``(succeeded, last lines of its output)``."""
    auth = aiohttp.BasicAuth(username, password or "") if username else None
    ws_url = urljoin(url, job).replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    message: dict[str, Any] = {"type": "spawn", "configuration": configuration}
    if port:
        message["port"] = port
    tail: list[str] = []
    try:
        async with aiohttp.ClientSession(timeout=JOB_TIMEOUT) as session, \
                session.ws_connect(ws_url, auth=auth, heartbeat=30) as ws:
            await ws.send_json(message)
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = msg.json()
                if data.get("event") == "line":
                    tail = [*tail, str(data.get("data") or "").rstrip()][-TAIL_LINES:]
                elif data.get("event") == "exit":
                    return int(data.get("code", 1)) == 0, tail
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        raise DeviceUnavailable(f"the ESPHome builder's {job} did not run: {exc or type(exc).__name__}") from exc
    return False, [*tail, f"the builder closed the {job} without an exit code"]


def builder_for(hub: Any) -> ESPHomeBuilderDriver | None:
    """The running builder driver, when one is set up and loaded."""
    for driver in hub.drivers.values():
        if isinstance(driver, ESPHomeBuilderDriver) and driver.answering and not driver.entry.disabled_by:
            return driver
    return None


class ESPHomeBuilderDriver(Driver):
    platform = "esphome_builder"
    poll_interval = 300.0
    check = staticmethod(check_dashboard)
    list_nodes: Callable[..., Awaitable[list[dict[str, Any]]]] = staticmethod(list_nodes)
    run_job: Callable[..., Awaitable[tuple[bool, list[str]]]] = staticmethod(run_job)

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        #: node name -> the builder's record of it (``configuration``, ``current_version``, ...).
        self.nodes: dict[str, dict[str, Any]] = {}
        #: node names being compiled and flashed now.
        self.installing: set[str] = set()
        #: Whether the dashboard answered the last check.
        self.answering = False
        self._jobs: set[asyncio.Task] = set()

    def _login(self) -> tuple[str, str | None, str | None]:
        return str(self.entry.data.get("url") or ""), self.entry.data.get("username"), self.entry.data.get("password")

    async def async_setup(self) -> None:
        url, username, password = self._login()
        if not url:
            raise HubError("esphome_builder entry has no url")
        self.answering = False
        try:
            result = await self.check(url, username, password)
            if result["status"] >= 500:
                raise DeviceUnavailable(f"the ESPHome builder answered HTTP {result['status']}")
        except HubError:
            self._republish_nodes()
            raise
        self.answering = True
        try:
            nodes = await self.list_nodes(url, username, password)
        except HubError as exc:
            # The link still works; only firmware installs are off, and the update entities say so.
            self.log.warning("ESPHome builder: %s; firmware updates cannot be offered", exc)
            nodes = []
        self.nodes = {str(n["name"]): n for n in nodes}
        self._republish_nodes()

    async def async_poll(self) -> None:
        try:
            await self.async_setup()
        except HubError as exc:
            self.entry.state, self.entry.reason = "setup_retry", str(exc)
        else:
            self.entry.state, self.entry.reason = "loaded", None

    async def async_unload(self) -> None:
        for job in list(self._jobs):
            job.cancel()
        self.nodes = {}
        self.answering = False
        self._republish_nodes()

    def _republish_nodes(self) -> None:
        """Let every ESPHome node show what the builder now offers it."""
        for driver in list(self.hub.drivers.values()):
            hook = getattr(driver, "publish_firmware_update", None)
            if callable(hook) and driver is not self:
                try:
                    hook()
                except Exception:  # one node's state must not stop the others'
                    self.log.exception("publishing the firmware update of %s failed", driver.entry.title)

    def firmware_for(self, node_name: str) -> dict[str, Any] | None:
        """``{"latest_version", "in_progress", "release_url"}`` for a node the builder has a config for."""
        node = self.nodes.get(node_name)
        if node is None or not node.get("current_version"):
            return None
        return {"latest_version": str(node["current_version"]), "in_progress": node_name in self.installing,
                "release_url": RELEASE_URL}

    def install(self, node_name: str) -> None:
        """Compile the node's config and flash it over the air, in the background (it takes minutes).

        Refuses at once what cannot work: a node the builder has no config for, or one already being flashed.
        A failed compile or upload is logged with the end of its output.
        """
        node = self.nodes.get(node_name)
        if node is None:
            raise HubError(f"the ESPHome builder has no config for {node_name}")
        if node_name in self.installing:
            raise HubError(f"{node_name} is already being updated")
        self.installing.add(node_name)
        task = asyncio.create_task(self._install(node_name, str(node["configuration"])),
                                   name=f"esphome_builder:install:{node_name}")
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        self._republish_nodes()

    async def _install(self, node_name: str, configuration: str) -> None:
        url, username, password = self._login()
        try:
            for job, port in (("compile", None), ("upload", "OTA")):
                ok, tail = await self.run_job(url, username, password, job, configuration, port)
                if not ok:
                    self.log.error("ESPHome builder: the %s of %s failed:\n%s", job, configuration, "\n".join(tail))
                    self.hub.bus.fire("homehub_firmware_update_failed",
                                      {"node": node_name, "step": job, "output": tail[-5:]})
                    return
            self.log.info("ESPHome builder: %s was compiled and flashed", configuration)
        except HubError as exc:
            self.log.error("ESPHome builder: updating %s failed: %s", configuration, exc)
            self.hub.bus.fire("homehub_firmware_update_failed", {"node": node_name, "step": "connect",
                                                                 "output": [str(exc)]})
        finally:
            self.installing.discard(node_name)
            with contextlib.suppress(Exception):
                self._republish_nodes()
