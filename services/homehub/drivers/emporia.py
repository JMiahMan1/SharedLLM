"""Emporia Vue energy monitor, through Emporia's cloud (pyemvue), as HA's
custom ``emporia_vue`` integration (magico13, v0.10.1) read it.

``entry.data`` = ``{email, password, enable_1m, enable_1d, enable_1mon}``.

Entities, by unique_id (the integration's ``CurrentVuePowerSensor.unique_id``):
- ``sensor.emporia_vue.instant.{gid}-{channel}``  power, the 1-minute average
- ``sensor.emporia_vue.1D.{gid}-{channel}``       energy today, kWh
- ``sensor.emporia_vue.1MON.{gid}-{channel}``     energy this billing month, kWh

``channel`` is the circuit number, ``1,2,3`` for the mains and ``Balance`` for
what the circuits do not account for.

Numbers are the integration's: the API reports kWh used over the minute, which
it turns into a rate with ``kWh * 60 * 1000`` (W). Some 1-minute sensors had
their display unit changed to kW in HA, and HA then reported (and automations
compared) kW; this driver publishes each entity in the display unit a person
chose (``options.sensor.unit_of_measurement``, ``Driver.user_unit``), so
``numeric_state ... above: 9`` on a kW sensor still means 9 kW. Without such a
choice it is the native W / kWh, never the unit an imported registry entry
carried. Each monitor's model and firmware are confirmed from the account's
device list (``_confirm_devices``).
Circuit readings are made positive unless the channel is bidirectional or is
the mains/balance (the integration's ``fix_usage_sign``). The day sensor is
re-read from the API every 15 minutes and, in between, adds each minute's kWh,
starting again from zero at local midnight, which is how the integration keeps
it current without a request a minute.

Attributes match what HA recorded for these sensors (statistics and the
energy dashboard depend on them): power sensors ``state_class: measurement``,
``device_class: power``; day and month sensors ``state_class: total``,
``device_class: energy`` and ``last_reset`` (local midnight; the billing-cycle
start day for the month). HA's integration set the month's ``last_reset`` to
this month's cycle day even before that day came, so its stored statistics
carry future reset times; the statistics compiler does not count leaving one
of those as a new cycle.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from services.homehub.core import UNKNOWN, Driver, EntityEntry, HubError

try:  # guarded so the tests can run without the library's cloud stack
    from pyemvue import PyEmVue
except ImportError:  # pragma: no cover - the image installs pyemvue
    PyEmVue = None  # type: ignore[assignment,misc]

MINUTE, DAY, MONTH = "1MIN", "1D", "1MON"
UID_SCALE = {"instant": MINUTE, "1D": DAY, "1MON": MONTH, "1MIN": MINUTE}
DAY_REFRESH = timedelta(minutes=15)
MONTH_REFRESH = timedelta(hours=1)
# pyemvue already puts (6 s connect, 10 s read) on every request; this bounds a
# whole call including its retries.
CALL_TIMEOUT = 60.0
UNSIGNED_CHANNELS = ("1,2,3", "Balance")


def parse_unique_id(unique_id: str) -> tuple[str, int, str] | None:
    """``sensor.emporia_vue.1D.100001-1,2,3`` -> ("1D", 100001, "1,2,3")."""
    prefix = "sensor.emporia_vue."
    if not unique_id.startswith(prefix):
        return None
    scale_part, _, rest = unique_id[len(prefix):].partition(".")
    gid, _, channel = rest.partition("-")
    scale = UID_SCALE.get(scale_part)
    if scale is None or not gid.isdigit() or not channel:
        return None
    return scale, int(gid), channel


def flatten_usage(usage_devices: dict[int, Any]) -> dict[tuple[int, str], float | None]:
    """(gid, channel) -> kWh over the scale, including nested devices (smart plugs)."""
    out: dict[tuple[int, str], float | None] = {}
    for dev in (usage_devices or {}).values():
        for ch in (getattr(dev, "channels", None) or {}).values():
            out[(int(ch.device_gid), str(ch.channel_num))] = ch.usage
            nested = getattr(ch, "nested_devices", None)
            if nested:
                out.update(flatten_usage(nested))
    return out


def month_reset(local_now: datetime, start_day: int) -> datetime:
    """Local midnight of the most recent billing-cycle start day."""
    start_day = max(1, int(start_day or 1))

    def on(year: int, month: int) -> datetime:
        day = min(start_day, calendar.monthrange(year, month)[1])
        return local_now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)

    candidate = on(local_now.year, local_now.month)
    if candidate > local_now:
        year, month = (local_now.year - 1, 12) if local_now.month == 1 else (local_now.year, local_now.month - 1)
        candidate = on(year, month)
    return candidate


class EmporiaDriver(Driver):
    platform = "emporia_vue"
    poll_interval = 60.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.vue: Any = None
        self.devices: dict[int, Any] = {}
        self.bidirectional: set[tuple[int, str]] = set()
        self.minute: dict[tuple[int, str], float] = {}
        self.day: dict[tuple[int, str], float] = {}
        self.month: dict[tuple[int, str], float] = {}
        self.day_reset: dict[int, datetime] = {}
        self.month_resets: dict[int, datetime] = {}
        self.day_fetched: datetime | None = None
        self.month_fetched: datetime | None = None
        self.ok: dict[str, bool] = {MINUTE: False, DAY: False, MONTH: False}

    def enabled(self, scale: str) -> bool:
        key = {MINUTE: "enable_1m", DAY: "enable_1d", MONTH: "enable_1mon"}[scale]
        return bool(self.entry.data.get(key, True))

    def _new_client(self) -> Any:
        if PyEmVue is None:
            raise HubError("pyemvue is not installed")
        return PyEmVue()

    async def _call(self, fn, *args) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), CALL_TIMEOUT)

    # -- setup / poll --------------------------------------------------------
    async def async_setup(self) -> None:
        data = self.entry.data
        vue = self._new_client()
        try:
            ok = await self._call(vue.login, data.get("email"), data.get("password"))
        except Exception as exc:
            self.log.error("Emporia cloud login failed for %s: %s", self.entry.title, exc)
            raise HubError(f"Emporia login failed: {exc}") from exc
        if not ok:
            self.log.error("Emporia cloud login refused for %s (check the email and password)", self.entry.title)
            raise HubError("Emporia login refused")
        self.vue = vue
        devices = await self._call(vue.get_devices)
        self.devices = {}
        for dev in devices:
            gid = int(dev.device_gid)
            if gid in self.devices:
                self.devices[gid].channels += dev.channels
            else:
                self.devices[gid] = dev
        self.bidirectional = {
            (gid, str(ch.channel_num))
            for gid, dev in self.devices.items() for ch in dev.channels
            if "bidirectional" in str(getattr(ch, "type", "")).lower()
            or "merged" in str(getattr(ch, "type", "")).lower()
        }
        self._confirm_devices()
        await self.refresh(force=True)

    def _confirm_devices(self) -> None:
        """Each monitor's model and firmware as the Emporia account reports them."""
        for gid, dev in self.devices.items():
            ident = ["emporia_vue", str(gid)]
            for device in self.hub.devices.values():
                if self.entry.entry_id in device.config_entries and ident in [list(x) for x in device.identifiers]:
                    self.confirm_device(device, manufacturer="Emporia", model=getattr(dev, "model", None) or None,
                                        sw_version=getattr(dev, "firmware", None) or None, identifiers=[ident])

    async def async_poll(self) -> None:
        await self.refresh()

    async def async_unload(self) -> None:
        self.vue = None

    def _tz(self, gid: int) -> ZoneInfo:
        name = getattr(self.devices.get(gid), "time_zone", "") or self.hub.time_zone
        try:
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("UTC")

    async def _usage(self, scale: str, now: datetime) -> dict[tuple[int, str], float | None]:
        gids = [str(g) for g in self.devices]
        # Two attempts inside pyemvue (it sleeps between them) instead of its five,
        # so one slow minute cannot hold the poll for a minute and a half.
        return flatten_usage(await self._call(
            lambda: self.vue.get_device_list_usage(gids, now, scale, "KilowattHours", 2)))

    def _signed(self, key: tuple[int, str], usage: float) -> float:
        if usage and key not in self.bidirectional and key[1] not in UNSIGNED_CHANNELS:
            return abs(usage)
        return usage

    def _store(self, target: dict[tuple[int, str], float], fresh: dict[tuple[int, str], float | None],
               keep_last: bool) -> None:
        for key, usage in fresh.items():
            if usage is None:
                # The API sometimes leaves a channel out of a minute; the
                # integration repeats the last value rather than reporting 0.
                usage = target.get(key, 0.0) if keep_last else 0.0
            target[key] = self._signed(key, float(usage))

    async def refresh(self, force: bool = False) -> None:
        if self.vue is None:
            return
        now = datetime.now(UTC)
        await self._refresh_scale(MINUTE, now)
        if self.enabled(DAY):
            if force or self.day_fetched is None or now - self.day_fetched >= DAY_REFRESH:
                if await self._refresh_scale(DAY, now):
                    self.day_fetched = now
                    for gid in self.devices:
                        self.day_reset[gid] = now.astimezone(self._tz(gid)).replace(
                            hour=0, minute=0, second=0, microsecond=0)
            else:
                self._integrate_minute(now)
        month_due = force or self.month_fetched is None or now - self.month_fetched >= MONTH_REFRESH
        if self.enabled(MONTH) and month_due and await self._refresh_scale(MONTH, now):
            self.month_fetched = now
            for gid, dev in self.devices.items():
                self.month_resets[gid] = month_reset(now.astimezone(self._tz(gid)),
                                                     getattr(dev, "billing_cycle_start_day", 1))
        self.publish_all()

    async def _refresh_scale(self, scale: str, now: datetime) -> bool:
        if not self.enabled(scale):
            return False
        try:
            fresh = await self._usage(scale, now)
        except Exception as exc:
            if self.ok[scale]:
                self.log.warning("Emporia %s usage failed: %s", scale, exc)
            self.ok[scale] = False
            return False
        if not fresh:
            self.ok[scale] = False
            return False
        # A circuit the account lists but the reply leaves out reads 0, as in
        # the integration (unused inputs report nothing at all).
        for gid, dev in self.devices.items():
            for ch in dev.channels:
                fresh.setdefault((gid, str(ch.channel_num)), 0.0)
        target = {MINUTE: self.minute, DAY: self.day, MONTH: self.month}[scale]
        self._store(target, fresh, keep_last=scale != MONTH)
        self.ok[scale] = True
        return True

    def _integrate_minute(self, now: datetime) -> None:
        if not self.ok[MINUTE]:
            return
        for key, kwh in self.minute.items():
            if key not in self.day:
                continue
            midnight = now.astimezone(self._tz(key[0])).replace(hour=0, minute=0, second=0, microsecond=0)
            last = self.day_reset.get(key[0])
            if last is None or midnight > last:
                self.day_reset[key[0]] = midnight
                for k in self.day:
                    if k[0] == key[0]:
                        self.day[k] = 0.0
            self.day[key] += kwh

    # -- publishing ----------------------------------------------------------
    def value_for(self, entity: EntityEntry) -> tuple[float | None, dict[str, Any]]:
        parsed = parse_unique_id(entity.unique_id)
        if parsed is None:
            return None, {}
        scale, gid, channel = parsed
        key = (gid, channel)
        if scale == MINUTE:
            kwh = self.minute.get(key) if self.ok[MINUTE] else None
            if kwh is None:
                return None, {}
            watts = kwh * 60 * 1000
            unit = self.user_unit(entity) if self.user_unit(entity) in ("W", "kW") else "W"
            value = watts / 1000 if unit == "kW" else watts
            return value, {"state_class": "measurement", "device_class": "power", "unit_of_measurement": unit}
        table = self.day if scale == DAY else self.month
        if not self.ok[scale] and key not in table:
            return None, {}
        kwh = table.get(key)
        if kwh is None:
            return None, {}
        reset = (self.day_reset if scale == DAY else self.month_resets).get(gid)
        unit = self.user_unit(entity) if self.user_unit(entity) in ("Wh", "kWh") else "kWh"
        value = kwh * 1000 if unit == "Wh" else kwh
        attrs = {"state_class": "total", "device_class": "energy", "unit_of_measurement": unit}
        if reset is not None:
            attrs["last_reset"] = reset.isoformat()
        return value, attrs

    def publish_all(self) -> None:
        for ent in self.entities:
            parsed = parse_unique_id(ent.unique_id)
            if parsed is None:
                continue
            value, attrs = self.value_for(ent)
            if value is None:
                if parsed[0] in self.ok and not self.ok[parsed[0]]:
                    self.mark_unavailable([ent])
                else:
                    self.publish(ent, UNKNOWN, attrs)
                continue
            self.publish(ent, value, attrs)
