"""Reolink cameras behind a Reolink Home Hub, through reolink-aio (HA's own library).

Unique ids are HA's: ``{hub_uid}_{camera_uid}_{key}`` for a camera's entities
(``9527000ABCDE12FG_95270006CAMERA01_motion``) and ``{hub_uid}_{key}`` for the
hub's own (``9527000ABCDE12FG_siren``). The camera uid is matched against
``host.camera_uid(channel)`` to find the channel, so a camera moved to another
hub port keeps its entities.

Events: the hub pushes motion and AI detections over Baichuan (TCP, port
9000), which is how HA hears battery cameras without waking them. The push is
subscribed when the hub allows it; the 30 s poll reads everything else and
covers the events when the push is down. Battery cameras are only woken for a
full state read once an hour, as HA does, because every wake costs battery.

Host: ``entry.data["host"]`` is ``ReoLink-Hub.local``, which only resolves
where mDNS works (not inside a Docker bridge network). ``entry.options["host"]``
overrides it with an address; set it to the hub's IP (and give the hub a DHCP
reservation).

Camera images and streams follow the contract at the top of ``mjpeg.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from reolink_aio.api import Host
from reolink_aio.enums import ConnectionEnum, DayNightEnum
from reolink_aio.exceptions import ReolinkError

from services.homehub.core import UNAVAILABLE, DeviceUnavailable, Driver, EntityEntry, ServiceNotFound
from services.homehub.drivers.mjpeg import CameraTokens, camera_attributes

#: Seconds between full state reads that wake battery cameras (HA's value).
BATTERY_WAKE_S = 3600.0
HOST_TIMEOUT_S = 15
RESOLVE_TIMEOUT_S = 5.0

#: Camera stream keys (the unique_id suffix of a camera entity).
CAMERA_STREAMS = {"sub": "sub", "main": "main", "ext": "ext", "snapshots_sub": "sub", "snapshots_main": "main",
                  "autotrack_sub": "autotrack_sub", "autotrack_main": "autotrack_main"}
#: binary_sensor key -> reolink-aio AI object type.
AI_TYPES = {"person": "people", "vehicle": "vehicle", "pet": "dog_cat", "animal": "dog_cat", "face": "face",
            "package": "package"}
#: switch key -> (getter, setter); both take the channel first.
SWITCHES = {
    "email": ("email_enabled", "set_email"),
    "ftp_upload": ("ftp_enabled", "set_ftp"),
    "buzzer": ("buzzer_enabled", "set_buzzer"),
    "ir_lights": ("ir_enabled", "set_ir_lights"),
    "manual_record": ("manual_record_enabled", "set_manual_record"),
    "push_notifications": ("push_enabled", "set_push"),
    "record": ("recording_enabled", "set_recording"),
    "record_audio": ("audio_record", "set_audio"),
    "siren_on_event": ("audio_alarm_enabled", "set_audio_alarm"),
}
#: number key -> (min, max) as HA's reolink number entities expose them.
NUMBER_RANGES = {"volume": (0, 100), "pir_sensitivity": (1, 100), "motion_sensitivity": (1, 50),
                 "ai_person_sensititvity": (0, 100), "ai_vehicle_sensititvity": (0, 100),
                 "ai_pet_sensititvity": (0, 100), "alarm_volume": (0, 100), "message_volume": (0, 100)}
DAY_NIGHT_OPTIONS = [e.name for e in DayNightEnum]  # auto, color, blackwhite (HA's option keys)
SIREN_FEATURES = 1 | 2 | 16 | 32  # TURN_ON | TURN_OFF | VOLUME_SET | DURATION


class ReolinkDriver(Driver):
    platform = "reolink"
    poll_interval = 30.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.host: Host | None = None
        self.tokens = CameraTokens()
        self._last_wake = 0.0
        self._channel_by_uid: dict[str, int] = {}

    # -- addressing -----------------------------------------------------------
    async def _address(self) -> str:
        override = self.entry.options.get("host")
        if override:
            return str(override)
        name = str(self.entry.data.get("host") or "")
        if not name.endswith(".local"):
            return name
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(RESOLVE_TIMEOUT_S):
                infos = await loop.getaddrinfo(name, None, family=2)  # AF_INET
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(f"{name} does not resolve here (mDNS); set entry.options['host'] "
                               f"to the hub's IP address") from exc
        return infos[0][4][0]

    @property
    def hub_uid(self) -> str:
        uid = getattr(self.host, "uid", None) if self.host is not None else None
        if uid and uid != "Unknown":
            return str(uid)
        return str(self.entry.data.get("uid") or "")

    def locate(self, entity: EntityEntry) -> tuple[int | None, str]:
        """(channel or None for the hub itself, key) from an HA unique_id."""
        uid = entity.unique_id
        prefix = f"{self.hub_uid}_"
        rest = uid[len(prefix):] if self.hub_uid and uid.startswith(prefix) else uid
        for cam_uid, ch in self._channel_by_uid.items():
            if rest.startswith(f"{cam_uid}_"):
                return ch, rest[len(cam_uid) + 1:]
        # ``{hub_uid}_{channel}_{key}`` is HA's form for a camera with no uid.
        head, _, tail = rest.partition("_")
        if head.isdigit() and tail and int(head) in self._channels():
            return int(head), tail
        # The hub's own LED is ``{hub_uid}_hub_status_led``.
        return None, "status_led" if rest == "hub_status_led" else rest

    def _channels(self) -> list[int]:
        return list(getattr(self.host, "channels", []) or [])

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        data = self.entry.data
        address = await self._address()
        try:
            bc_conn = ConnectionEnum(str(data.get("baichuan_connection") or "unknown"))
        except ValueError:
            bc_conn = ConnectionEnum.unknown
        kwargs: dict[str, Any] = {
            "port": data.get("port"), "use_https": data.get("use_https"),
            "protocol": str(self.entry.options.get("protocol") or "rtsp"),
            "timeout": HOST_TIMEOUT_S, "bc_port": int(data.get("baichuan_port") or 9000),
            "bc_only": bool(data.get("baichuan_only")), "bc_connection": bc_conn,
        }
        if data.get("uid"):
            kwargs["uid"] = str(data["uid"])
        self.host = Host(address, str(data.get("username") or ""), str(data.get("password") or ""), **kwargs)
        try:
            await self.host.get_host_data()
            await self.host.get_states()
        except ReolinkError as exc:
            raise DeviceUnavailable(f"Reolink hub at {address}: {exc}") from exc
        self._last_wake = time.monotonic()
        self._channel_by_uid = {}
        for ch in self._channels():
            cam_uid = self.host.camera_uid(ch)
            if cam_uid and cam_uid != "Unknown":
                self._channel_by_uid[str(cam_uid)] = ch
        try:
            self.host.baichuan.register_callback("homehub", self._on_push)
            await self.host.baichuan.subscribe_events()
        except Exception as exc:  # the poll still reads events, only slower
            self.log.warning("Reolink push events unavailable, polling instead: %s", exc)
        self._publish_all()

    async def async_poll(self) -> None:
        host = self.host
        if host is None:
            return
        battery = [ch for ch in self._channels() if host.supported(ch, "battery")]
        wake_all = time.monotonic() - self._last_wake >= BATTERY_WAKE_S
        wake = {ch: (wake_all or ch not in battery) for ch in host.stream_channels}
        try:
            await host.get_states(wake=wake)
            if wake_all:
                self._last_wake = time.monotonic()
            if not host.baichuan.events_active:
                await host.get_motion_state_all_ch()
        except ReolinkError as exc:
            self.mark_unavailable()
            raise DeviceUnavailable(f"Reolink hub: {exc}") from exc
        for ent in self.entities:
            if ent.domain == "camera" and self.tokens.due(ent.entity_id):
                self.tokens.rotate(ent.entity_id)
        self._publish_all()

    async def async_unload(self) -> None:
        host, self.host = self.host, None
        if host is None:
            return
        with contextlib.suppress(Exception):
            host.baichuan.unregister_callback("homehub")
            await host.baichuan.unsubscribe_events()
        with contextlib.suppress(Exception):
            await host.logout()

    def _on_push(self) -> None:
        # reolink-aio calls this from its protocol handler; publishing is cheap and sync.
        for ent in self.entities:
            if ent.domain == "binary_sensor":
                self._publish_one(ent)

    # -- states ---------------------------------------------------------------
    def _publish_all(self) -> None:
        for ent in self.entities:
            try:
                self._publish_one(ent)
            except Exception:
                self.log.exception("publishing %s failed", ent.entity_id)

    def _publish_one(self, ent: EntityEntry) -> None:
        host = self.host
        if host is None:
            return
        ch, key = self.locate(ent)
        if ch is not None and not host.camera_online(ch):
            cur = self.hub.states.get(ent.entity_id)
            self.publish(ent, UNAVAILABLE, dict(cur.attributes) if cur else {})
            return
        result = self._read(ent.domain, ch, key)
        if result is not None:
            self.publish(ent, *result)

    def _read(self, domain: str, ch: int | None, key: str) -> tuple[Any, dict[str, Any]] | None:
        host = self.host
        assert host is not None
        if domain == "camera" and ch is not None and key in CAMERA_STREAMS:
            eid = self._entity_id_for(ch, key)
            extra = {"brand": "Reolink", "model_name": host.camera_model(ch), "frontend_stream_type": "hls"}
            return "idle", camera_attributes(eid, self.tokens.current(eid), supported_features=2, extra=extra)
        if domain == "binary_sensor" and ch is not None:
            if key == "motion":
                return _on_off(host.motion_detected(ch)), {}
            if key == "sleep":
                return _on_off(host.sleeping(ch)), {}
            if key in AI_TYPES:
                return _on_off(host.ai_detected(ch, AI_TYPES[key])), {}
            return None
        if domain == "sensor" and ch is not None and key == "battery_percent":
            value = host.battery_percentage(ch)
            return ("unknown" if value is None else value), {"unit_of_measurement": "%", "device_class": "battery",
                                                              "state_class": "measurement"}
        if domain == "light" and key == "status_led":
            on = host.state_light if ch is None else host.status_led_enabled(ch)
            return _on_off(on), {"color_mode": "onoff" if on else None, "supported_color_modes": ["onoff"],
                                 "supported_features": 0}
        if domain == "siren":
            return "unknown", {"supported_features": SIREN_FEATURES}
        if domain == "switch" and ch is not None and key in SWITCHES:
            return _on_off(getattr(host, SWITCHES[key][0])(ch)), {}
        if domain == "number" and key in NUMBER_RANGES:
            value = self._number(ch, key)
            if value is None:
                return None
            lo, hi = NUMBER_RANGES[key]
            return value, {"min": lo, "max": hi, "step": 1, "mode": "auto"}
        if domain == "select" and ch is not None and key == "day_night_mode":
            raw = host.daynight_state(ch)
            option = next((e.name for e in DayNightEnum if e.value == raw), None)
            return (option or "unknown"), {"options": DAY_NIGHT_OPTIONS}
        return None

    def _number(self, ch: int | None, key: str) -> Any:
        host = self.host
        assert host is not None
        if ch is None:
            if key == "alarm_volume":
                return host.alarm_volume
            if key == "message_volume":
                return host.message_volume
            return None
        if key == "volume":
            return host.volume(ch)
        if key == "pir_sensitivity":
            return host.pir_sensitivity(ch)
        if key == "motion_sensitivity":
            return host.md_sensitivity(ch)
        if key.startswith("ai_") and key.endswith("_sensititvity"):  # HA's own spelling
            return host.ai_sensitivity(ch, AI_TYPES.get(key[3:-len("_sensititvity")], key))
        return None

    def _entity_id_for(self, ch: int, key: str) -> str:
        for ent in self.entities:
            if self.locate(ent) == (ch, key):
                return ent.entity_id
        return f"camera.reolink_{ch}_{key}"

    def access_tokens(self, entity_id: str) -> list[str]:
        return self.tokens.valid(entity_id)

    # -- services -------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        host = self.host
        if host is None:
            raise DeviceUnavailable("Reolink hub is not connected")
        ch, key = self.locate(entity)
        try:
            await self._run(host, entity, domain, service, ch, key, data)
        except ReolinkError as exc:
            raise DeviceUnavailable(f"{entity.entity_id}: {exc}") from exc
        if domain != "siren":
            with contextlib.suppress(ReolinkError):
                await host.get_states(wake={c: c == ch or not host.supported(c, "battery")
                                            for c in host.stream_channels})
        self._publish_one(entity)

    async def _run(self, host: Host, entity: EntityEntry, domain: str, service: str, ch: int | None, key: str,
                   data: dict[str, Any]) -> None:
        def on_for(svc: str, current: bool) -> bool:
            return not current if svc == "toggle" else svc == "turn_on"

        if domain == "siren" and service in ("turn_on", "turn_off", "toggle"):
            if service == "turn_off":
                await host.set_siren(ch, False, None)
                return
            if ch is not None and "volume_level" in data:
                await host.set_volume(ch, volume=round(float(data["volume_level"]) * 100))
            duration = data.get("duration")
            await host.set_siren(ch, True, int(duration) if duration is not None else 2)
            return
        if domain == "light" and key == "status_led" and service in ("turn_on", "turn_off", "toggle"):
            if ch is None:
                await host.set_state_light(on_for(service, host.state_light))
            else:
                await host.set_status_led(ch, on_for(service, host.status_led_enabled(ch)))
            return
        if domain == "switch" and ch is not None and key in SWITCHES and service in ("turn_on", "turn_off", "toggle"):
            getter, setter = SWITCHES[key]
            await getattr(host, setter)(ch, on_for(service, bool(getattr(host, getter)(ch))))
            return
        if domain == "number" and service == "set_value" and ch is not None and key in NUMBER_RANGES:
            value = round(float(data["value"]))
            if key == "volume":
                await host.set_volume(ch, volume=value)
            elif key == "pir_sensitivity":
                await host.set_pir(ch, sensitivity=value)
            elif key == "motion_sensitivity":
                await host.set_md_sensitivity(ch, value)
            elif key.startswith("ai_"):
                await host.set_ai_sensitivity(ch, value, AI_TYPES.get(key[3:-len("_sensititvity")], key))
            return
        if domain == "select" and service == "select_option" and ch is not None and key == "day_night_mode":
            option = str(data.get("option"))
            if option not in DAY_NIGHT_OPTIONS:
                raise ServiceNotFound(f"{entity.entity_id}: unknown option {option!r}")
            await host.set_daynight(ch, DayNightEnum[option].value)
            return
        raise ServiceNotFound(f"reolink cannot run {domain}.{service} on {entity.entity_id}")

    # -- the camera contract --------------------------------------------------
    def _camera(self, entity: EntityEntry) -> tuple[Host, int, str]:
        host = self.host
        if host is None:
            raise DeviceUnavailable("Reolink hub is not connected")
        ch, key = self.locate(entity)
        if ch is None or key not in CAMERA_STREAMS:
            raise ServiceNotFound(f"{entity.entity_id} is not a Reolink camera stream")
        return host, ch, CAMERA_STREAMS[key]

    async def async_camera_image(self, entity: EntityEntry) -> bytes:
        host, ch, stream = self._camera(entity)
        try:
            image = await host.get_snapshot(ch, stream if stream in ("main", "sub") else "sub")
        except ReolinkError as exc:
            raise DeviceUnavailable(f"{entity.entity_id}: {exc}") from exc
        if not image:
            raise DeviceUnavailable(f"{entity.entity_id}: the camera returned no image")
        return image

    async def async_camera_stream_url(self, entity: EntityEntry) -> str | None:
        host, ch, stream = self._camera(entity)
        try:
            return await host.get_stream_source(ch, stream)
        except ReolinkError as exc:
            raise DeviceUnavailable(f"{entity.entity_id}: {exc}") from exc


def _on_off(value: Any) -> str:
    return "on" if value else "off"
