"""SRP (Salt River Project) energy usage, through SRP's customer site (srpenergy).

``entry.data`` = ``{id, is_tou, name, username, password}``; ``id`` is the
9-digit billing account.

One entity, ``{entry_id}_total_usage``: the kWh used over the last day, as HA's
core ``srp_energy`` coordinator computed it (sum the hourly rows SRP returns
from 24 hours ago to now, Phoenix time). SRP publishes usage hours late and
once a day at best, so this polls hourly.

srpenergy uses ``requests`` with no timeout at all, so the client module is
handed a ``requests`` stand-in whose sessions put one on every request.
"""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from services.homehub.core import Driver, HubError

try:  # guarded so the tests can run without the library
    from srpenergy import client as srp_client
except ImportError:  # pragma: no cover - the image installs srpenergy
    srp_client = None  # type: ignore[assignment]

PHOENIX = ZoneInfo("America/Phoenix")
REQUEST_TIMEOUT = (10, 30)  # requests' (connect, read)
CALL_TIMEOUT = 120.0


class _TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return super().request(method, url, **kwargs)


def _with_timeouts(module: Any) -> None:
    """Give the srpenergy client module a ``requests`` whose sessions time out."""
    if module is None or getattr(module, "_homehub_timeouts", False):
        return
    shim = types.SimpleNamespace(**{k: getattr(requests, k) for k in dir(requests) if not k.startswith("__")})
    shim.Session = _TimeoutSession
    shim.session = _TimeoutSession
    module.requests = shim
    module._homehub_timeouts = True


def daily_usage(rows: list[tuple]) -> float:
    """Sum the kWh column of srpenergy's (date, time, iso, kwh, cost) rows."""
    return sum(float(row[3]) for row in rows)


class SrpEnergyDriver(Driver):
    platform = "srp_energy"
    poll_interval = 3600.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.client: Any = None
        self.failing = False

    def _new_client(self) -> Any:
        if srp_client is None:
            raise HubError("srpenergy is not installed")
        _with_timeouts(srp_client)
        d = self.entry.data
        return srp_client.SrpEnergyClient(str(d.get("id", "")), d.get("username"), d.get("password"))

    async def async_setup(self) -> None:
        try:
            self.client = self._new_client()
        except (TypeError, ValueError) as exc:
            self.log.error("SRP account settings are invalid for %s: %s", self.entry.title, exc)
            raise HubError(f"SRP settings invalid: {exc}") from exc
        await self.refresh(raise_on_error=True)

    async def async_poll(self) -> None:
        await self.refresh()

    async def refresh(self, raise_on_error: bool = False) -> None:
        end = datetime.now(PHOENIX)
        start = end - timedelta(days=1)
        is_tou = bool(self.entry.data.get("is_tou", False))
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(self.client.usage, start, end, is_tou), CALL_TIMEOUT)
            value = round(daily_usage(rows), 2)
        except Exception as exc:
            if not self.failing:
                self.log.error("SRP usage fetch failed for %s: %s", self.entry.title, exc)
            self.failing = True
            self.mark_unavailable()
            if raise_on_error:
                raise HubError(f"SRP usage fetch failed: {exc}") from exc
            return
        self.failing = False
        for ent in self.entities:
            if ent.unique_id.endswith("_total_usage"):
                self.publish(ent, value, {"state_class": "total_increasing",
                                          "unit_of_measurement": ent.unit_of_measurement or "kWh",
                                          "device_class": "energy"})
