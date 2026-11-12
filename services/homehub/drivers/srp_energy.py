"""SRP (Salt River Project) energy usage, through SRP's customer site (srpenergy).

``entry.data`` = ``{id, is_tou, name, username, password}``; ``id`` is the
9-digit billing account.

One entity, ``{entry_id}_total_usage``: the kWh used over the last day, as HA's
core ``srp_energy`` coordinator computed it (sum the hourly rows SRP returns
from 24 hours ago to now, Phoenix time). SRP publishes usage hours late and
once a day at best, so this polls hourly.

srpenergy uses ``requests`` with no timeout at all, so the client module is
handed a ``requests`` stand-in whose sessions put one on every request.

Long-term statistics, as HA's integration kept them: SRP's hourly readings
become external statistics ``srp_energy:<account>_energy_consumption`` (kWh)
and ``srp_energy:<account>_energy_cost``, plus one
``..._energy_consumption_<period>`` per time-of-use period the account is
billed in (``on_peak``, ``off_peak``, ``shoulder``, ``super_off_peak``), as
SRP itself classified each hour. The energy dashboard's on-peak/off-peak
split comes from these, so no plan's hours are written down anywhere. The
rows are read straight from SRP's ``usage/hourlydetail`` (the call srpenergy
makes), because srpenergy's own ``usage()`` drops the period columns. Hours
SRP has not published yet read 0 kWh; they are held back until they do not.

Options: ``statistics`` (default on), ``statistics_backfill_days`` (how far
back the first fetch reaches when nothing is stored yet, default 30) and
``tou_backfill_days`` (the same for the time-of-use period statistics, which
HA never kept, default 400 so every season is seen). Catch-ups are fetched a
month at a time.
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

#: SRP reports hours in its customers' local time without an offset. Which
#: zone that is comes from the entry's ``time_zone`` option, else the hub's
#: own time zone (``SrpEnergyDriver.tz``). There is no guessed default: a
#: wrong zone would shift every hour's on-peak/off-peak split silently.
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


#: SRP's period columns -> the suffix of their statistic.
PERIOD_FIELDS = {"onPeak": "on_peak", "offPeak": "off_peak", "shoulder": "shoulder", "superOffPeak": "super_off_peak"}
#: Never store the most recent hours: SRP fills them in late.
SETTLE = timedelta(hours=2)
#: SRP's site answers a month of hourly rows comfortably; longer catch-ups go a month at a time.
FETCH_CHUNK = timedelta(days=31)


def _local(iso: str, tz: Any) -> datetime:
    dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def normalize_hourly(raw: list[dict[str, Any]], tz: Any) -> list[dict[str, Any]]:
    """SRP's hourlyUsageList -> ``{start, kwh, cost, periods}``; totals built from the parts when SRP leaves them 0."""
    out = []
    for row in raw or []:
        periods = {suffix: float(row.get(f"{field}Kwh") or 0.0) for field, suffix in PERIOD_FIELDS.items()}
        kwh = float(row.get("totalKwh") or 0.0) or sum(periods.values())
        cost = float(row.get("totalCost") or 0.0) or sum(float(row.get(f"{f}Cost") or 0.0) for f in PERIOD_FIELDS)
        out.append({"start": _local(row["date"], tz), "kwh": kwh, "cost": cost, "periods": periods})
    return out


def fetch_hourly(client: Any, start: datetime, end: datetime, tz: Any) -> list[dict[str, Any]]:
    """SRP's hourly detail, with the period columns, through srpenergy's own endpoints and headers (blocking)."""
    if srp_client is None or not hasattr(client, "_check_response"):
        raise AttributeError("srpenergy client without the hourly detail endpoint")
    from urllib.parse import unquote

    with _TimeoutSession() as session:
        session.headers.update(srp_client.BROWSER_HEADERS)
        resp = session.post(srp_client.BASE_USAGE_URL + "login/authorize",
                            data={"username": client.username, "password": client.password})
        client._check_response(resp, "login/authorize")
        resp = session.get(srp_client.BASE_USAGE_URL + "login/antiforgerytoken")
        if "xsrf-token" not in resp.cookies:
            raise HubError("SRP did not hand out an XSRF token")
        token = unquote(resp.cookies["xsrf-token"])
        resp = session.get(srp_client.BASE_USAGE_URL + "usage/hourlydetail",
                           params={"billaccount": client.accountid, "beginDate": start.strftime("%m-%d-%Y"),
                                   "endDate": end.strftime("%m-%d-%Y")},
                           headers={"x-xsrf-token": token})
        client._check_response(resp, "usage/hourlydetail")
        return normalize_hourly(resp.json().get("hourlyUsageList") or [], tz)


def from_usage_tuples(rows: list[tuple], tz: Any) -> list[dict[str, Any]]:
    """srpenergy's ``usage()`` tuples in the same shape (no period columns)."""
    return [{"start": _local(r[2], tz), "kwh": float(r[3]), "cost": float(r[4] or 0.0), "periods": {}} for r in rows]


def published(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The hours SRP has really published: up to the last one with usage, and not the last ``SETTLE``."""
    rows = sorted((r for r in rows if r["start"] + timedelta(hours=1) <= now - SETTLE), key=lambda r: r["start"])
    while rows and not rows[-1]["kwh"]:
        rows.pop()
    return rows


class SrpEnergyDriver(Driver):
    platform = "srp_energy"
    poll_interval = 3600.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.client: Any = None
        #: Whether the last fetch reached the end of its window (a long catch-up can stop early).
        self.fetch_complete = False
        self.failing = False
        zone = entry.options.get("time_zone") or hub.time_zone
        if not zone:
            raise HubError("srp_energy needs a time zone: set the entry's time_zone option or the hub's TZ")
        self.tz = ZoneInfo(str(zone))

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

    def statistic_id(self, suffix: str) -> str:
        return f"srp_energy:{self.entry.data.get('id', self.entry.entry_id)}_{suffix}"

    def _statistics(self) -> Any:
        if not self.entry.options.get("statistics", True):
            return None
        return getattr(self.hub, "statistics", None)

    def _fetch_start(self, day_start: datetime) -> datetime:
        """Where the next fetch starts: the oldest hour any of this account's statistics still needs."""
        stats = self._statistics()
        if stats is None:
            return day_start
        opts = self.entry.options
        starts = []
        last = stats.last_start(self.statistic_id("energy_consumption"))
        floor = day_start - timedelta(days=float(opts.get("statistics_backfill_days", 30)))
        starts.append(max(floor, last.astimezone(self.tz) + timedelta(hours=1)) if last else floor)
        if self.entry.data.get("is_tou"):
            # The period statistics are the hub's own (HA kept none), so the first fetch reaches
            # back far enough to see every season's time-of-use hours.
            tou_floor = day_start - timedelta(days=float(opts.get("tou_backfill_days", 400)))
            lasts = [stats.last_start(self.statistic_id(f"energy_consumption_{p}")) for p in PERIOD_FIELDS.values()]
            known = [x for x in lasts if x is not None]
            if known:
                starts.append(max(tou_floor, min(known).astimezone(self.tz) + timedelta(hours=1)))
            elif not stats.get_kv(self._tou_marker()):
                starts.append(tou_floor)  # once: an account billed in no period must not refetch a year an hour
        return min(min(starts), day_start)

    def _tou_marker(self) -> str:
        return f"srp_energy:{self.entry.data.get('id', self.entry.entry_id)}:tou_backfilled"

    async def _rows(self, start: datetime, end: datetime, is_tou: bool) -> tuple[list[dict[str, Any]], bool]:
        """(hourly rows, whether they carry SRP's period columns), fetched a month at a time.

        A long catch-up keeps the months it got when a later one fails; the
        next poll starts again after the last stored hour.
        """
        rows: list[dict[str, Any]] = []
        cursor = start
        self.fetch_complete = False
        while True:
            chunk_end = min(end, cursor + FETCH_CHUNK)
            try:
                got = await asyncio.wait_for(asyncio.to_thread(fetch_hourly, self.client, cursor, chunk_end, self.tz),
                                             CALL_TIMEOUT)
            except AttributeError:
                old = await asyncio.wait_for(asyncio.to_thread(self.client.usage, start, end, is_tou), CALL_TIMEOUT)
                return from_usage_tuples(old, self.tz), False
            except Exception as exc:
                if not rows:
                    raise
                self.log.warning("SRP catch-up stopped at %s: %s", cursor.date(), exc)
                return rows, True
            rows.extend(got)
            if chunk_end >= end:
                break
            cursor = (chunk_end + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.fetch_complete = True
        seen: dict[datetime, dict[str, Any]] = {r["start"]: r for r in rows}
        return [seen[k] for k in sorted(seen)], True

    def _record_statistics(self, rows: list[dict[str, Any]], detailed: bool, now: datetime) -> None:
        stats = self._statistics()
        if stats is None:
            return
        rows = published(rows, now)
        if not rows:
            return
        title = self.entry.title or "SRP"
        stats.add_hourly_amounts(self.statistic_id("energy_consumption"), source="srp_energy", unit="kWh",
                                 name=f"{title} energy consumption", amounts=[(r["start"], r["kwh"]) for r in rows])
        stats.add_hourly_amounts(self.statistic_id("energy_cost"), source="srp_energy", unit="USD",
                                 name=f"{title} energy cost", amounts=[(r["start"], r["cost"]) for r in rows])
        if not detailed:
            return
        for suffix in PERIOD_FIELDS.values():
            sid = self.statistic_id(f"energy_consumption_{suffix}")
            if stats.get_meta(sid) is None and not any(r["periods"].get(suffix) for r in rows):
                continue  # a period this account is not billed in
            stats.add_hourly_amounts(sid, source="srp_energy", unit="kWh",
                                     name=f"{title} energy consumption {suffix.replace('_', ' ')}",
                                     amounts=[(r["start"], r["periods"].get(suffix, 0.0)) for r in rows])

    async def refresh(self, raise_on_error: bool = False) -> None:
        end = datetime.now(self.tz)
        start = end - timedelta(days=1)
        is_tou = bool(self.entry.data.get("is_tou", False))
        try:
            fetch_from = self._fetch_start(start)
            rows, detailed = await self._rows(fetch_from, end, is_tou)
            # The sensor is what HA showed: every hour SRP returns for yesterday and today (a
            # longer fetch, catching statistics up, is cut back to that).
            day_rows = rows if fetch_from >= start else [r for r in rows if r["start"].date() >= start.date()]
            value = round(sum(r["kwh"] for r in day_rows), 2)
        except Exception as exc:
            if not self.failing:
                self.log.error("SRP usage fetch failed for %s: %s", self.entry.title, exc)
            self.failing = True
            self.mark_unavailable()
            if raise_on_error:
                raise HubError(f"SRP usage fetch failed: {exc}") from exc
            return
        self.failing = False
        try:
            await asyncio.to_thread(self._record_statistics, rows, detailed, end)
            if detailed and is_tou and self.fetch_complete and (stats := self._statistics()) is not None:
                stats.set_kv(self._tou_marker(), True)
        except Exception:
            self.log.exception("recording SRP statistics failed")
        for ent in self.entities:
            if ent.unique_id.endswith("_total_usage"):
                self.publish(ent, value, {"state_class": "total_increasing",
                                          "unit_of_measurement": ent.unit_of_measurement or "kWh",
                                          "device_class": "energy"})
