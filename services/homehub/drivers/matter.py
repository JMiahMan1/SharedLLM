"""Matter, through the Matter Server's WebSocket API.

The hub does not speak Matter itself. The fabric (its root CA, the controller's
operational credentials, every node's session data) lives in the Matter Server
that Home Assistant's add-on ran, and that server keeps working without Home
Assistant: we run the same server image on the Jarvis host with a copy of its
storage and talk to it the way Home Assistant did, over ``ws://host:5580/ws``.
See ``services/homehub/MATTER.md`` for running it.

The server is matter.js-based since add-on 9.0 (add-on 9.3.0 = matterjs-server
1.5.5), and keeps the Python Matter Server's WebSocket protocol:

- on connect the server sends one ``server_info`` message;
- a command is ``{"message_id", "command", "args"}``, answered by
  ``{"message_id", "result"}`` or ``{"message_id", "error_code", "details"}``;
- ``start_listening`` answers with every node and then streams events
  (``attribute_updated`` = ``[node_id, "endpoint/cluster/attribute", value]``,
  ``node_added``/``node_updated`` = the node, ``node_removed`` = its id);
- a node is ``{"node_id", "available", "attributes": {"1/6/0": true, ...}}``.

Entities are found by Home Assistant's unique_id,
``{compressed_fabric_id:016X}-{node_id:016X}-MatterNodeDevice-{endpoint}-{key}-{cluster}-{attribute}``,
which names the node, the endpoint and the attribute the entity reads, so no
device mapping has to be guessed.

Commands are sent with the names and payload keys Home Assistant's own Matter
integration sends (the chip cluster class names, ``MoveToLevelWithOnOff`` with
``level``/``transitionTime``/...). The matter.js server accepts them unchanged,
which is what "drop-in replacement" means for HA.

**What a node is and can do comes from the node.** Each time a node's data
arrives, its BasicInformation (maker, product, firmware, hardware version,
serial) is confirmed onto its device (``confirm_device``) and every entity's
device class, unit, state class, colour modes, colour-temperature range and
ranges are recomputed from the node's own clusters (``node_entity_specs``, the
same function that registers a newly commissioned node) and confirmed onto the
registry. Registry values the import carried are never read for this: an
imported entity whose kind ``node_entity_specs`` does not model gets no ranges
or classes until it does, rather than Home Assistant's.
"""
from __future__ import annotations

import asyncio
import colorsys
import contextlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

from services.homehub.core import (
    UNAVAILABLE,
    UNKNOWN,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    HubError,
    ServiceNotFound,
)

# Matter cluster ids
IDENTIFY = 3
ON_OFF = 6
LEVEL_CONTROL = 8
BASIC_INFORMATION = 40
GENERAL_DIAGNOSTICS = 51
WIFI_DIAGNOSTICS = 54
COLOR_CONTROL = 0x300  # 768

# ColorControl FeatureMap bits and ColorMode values
CC_FEATURE_HS = 1 << 0
CC_FEATURE_XY = 1 << 3
CC_FEATURE_CT = 1 << 4
CC_MODE = {0: "hs", 1: "xy", 2: "color_temp"}
# ExecuteIfOff: lets a colour be set while the light is off, so "turn on in warm
# white" does not flash the old colour first. Home Assistant sends the same.
EXECUTE_IF_OFF = 1

LIGHT_FEATURE_TRANSITION = 32  # LightEntityFeature.TRANSITION
UPDATE_FEATURES = 23  # what HA's Matter update entity advertises (the registry agrees)

# A level of 1-254 maps onto brightness 1-255 the way Home Assistant does it.
DEFAULT_MIN_LEVEL = 1
DEFAULT_MAX_LEVEL = 254
DEFAULT_MIN_MIREDS = 153  # 6535 K
DEFAULT_MAX_MIREDS = 500  # 2000 K
IDENTIFY_SECONDS = 15

# Server error codes (python-matter-server's, kept by matter.js). These say the
# node did not answer, which callers treat as "unavailable"; anything else is a
# command the server refused.
UNREACHABLE_ERRORS = {3: "NodeNotReady", 4: "NodeNotResolving", 7: "SDKStackError"}

COMMAND_TIMEOUT = 30.0
#: Commissioning joins the device and interviews it before the server answers.
COMMISSION_TIMEOUT = 180.0
LISTEN_TIMEOUT = 120.0  # start_listening dumps every node; the first start can be slow
RECONNECT_DELAYS = (1, 2, 5, 10, 30, 60)

UNIQUE_ID_RE = re.compile(
    r"^(?P<fabric>[0-9A-Fa-f]{16})-(?P<node>[0-9A-Fa-f]{16})-MatterNodeDevice-"
    r"(?P<endpoint>\d+)-(?P<key>[A-Za-z0-9_]+)-(?P<cluster>\d+)-(?P<attribute>\d+)$"
)


@dataclass(frozen=True)
class MatterRef:
    """What a Matter unique_id says about its entity."""

    fabric: int
    node_id: int
    endpoint: int
    key: str
    cluster: int
    attribute: int

    def path(self, attribute: int | None = None, cluster: int | None = None) -> str:
        return f"{self.endpoint}/{self.cluster if cluster is None else cluster}/" \
               f"{self.attribute if attribute is None else attribute}"


def parse_unique_id(unique_id: str) -> MatterRef | None:
    m = UNIQUE_ID_RE.match(unique_id or "")
    if m is None:
        return None
    return MatterRef(int(m["fabric"], 16), int(m["node"], 16), int(m["endpoint"]), m["key"],
                     int(m["cluster"]), int(m["attribute"]))


# -- colour maths (Home Assistant's formulas, so states read the same) --------

def _renormalize(value: float, src: tuple[float, float], dst: tuple[float, float]) -> float:
    if src[1] == src[0]:
        return dst[1]
    return (value - src[0]) * (dst[1] - dst[0]) / (src[1] - src[0]) + dst[0]


def mired_to_kelvin(mireds: float) -> int:
    return round(1_000_000 / mireds)


def kelvin_to_mired(kelvin: float) -> int:
    return round(1_000_000 / kelvin)


def kelvin_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Tanner Helland's approximation (what HA's color_temperature_to_rgb uses)."""
    temp = max(1000.0, min(40000.0, kelvin)) / 100
    if temp <= 66:
        red = 255.0
        green = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        red = 329.698727446 * ((temp - 60) ** -0.1332047592)
        green = 288.1221695283 * ((temp - 60) ** -0.0755148492)
    if temp >= 66:
        blue = 255.0
    elif temp <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(temp - 10) - 305.0447927307
    return tuple(int(max(0.0, min(255.0, c))) for c in (red, green, blue))  # type: ignore[return-value]


def rgb_to_xy(rgb: tuple[float, float, float]) -> tuple[float, float]:
    def lin(c: float) -> float:
        c /= 255
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92

    r, g, b = (lin(float(c)) for c in rgb)
    x_ = r * 0.664511 + g * 0.154324 + b * 0.162028
    y_ = r * 0.283881 + g * 0.668433 + b * 0.047685
    z_ = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x_ + y_ + z_
    if total == 0:
        return (0.0, 0.0)
    return (round(x_ / total, 4), round(y_ / total, 4))


def xy_to_rgb(x: float, y: float) -> tuple[int, int, int]:
    if y == 0:
        return (0, 0, 0)
    big_y = 1.0
    big_x = (big_y / y) * x
    big_z = (big_y / y) * (1 - x - y)
    r = big_x * 1.656492 - big_y * 0.354851 - big_z * 0.255038
    g = -big_x * 0.707196 + big_y * 1.655397 + big_z * 0.036152
    b = big_x * 0.051713 - big_y * 0.121364 + big_z * 1.011530

    def gamma(c: float) -> float:
        return 12.92 * c if c <= 0.0031308 else (1.055 * c ** (1 / 2.4)) - 0.055

    rgb = [max(0.0, gamma(c)) for c in (r, g, b)]
    top = max(rgb)
    if top > 0:
        rgb = [c / top for c in rgb]
    return tuple(round(c * 255) for c in rgb)  # type: ignore[return-value]


def rgb_to_hs(rgb: tuple[float, float, float]) -> tuple[float, float]:
    h, s, _v = colorsys.rgb_to_hsv(*(float(c) / 255 for c in rgb))
    return (round(h * 360, 3), round(s * 100, 3))


def hs_to_rgb(hs: tuple[float, float]) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(float(hs[0]) / 360, float(hs[1]) / 100, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


# -- number and select entities ----------------------------------------------
# key -> (to_ha, to_device). Home Assistant's own conversions: transition times
# are tenths of a second on the wire and seconds in HA; on_level and
# power_on_level use 255 for "null" (no fixed level, use the previous one); the
# power-on colour temperature is mireds on the wire and kelvin in HA.

def _tenths_to_s(v: Any) -> Any:
    return None if v is None else v / 10


def _s_to_tenths(v: float) -> int:
    return round(float(v) * 10)


def _null_is_255(v: Any) -> Any:
    return 255 if v is None else v


def _255_is_null(v: float) -> int | None:
    return None if int(v) == 255 else int(v)


def _mireds_to_k(v: Any) -> Any:
    return None if not v else mired_to_kelvin(v)


def _k_to_mireds(v: float) -> int | None:
    return None if not v else kelvin_to_mired(v)


NUMBER_CONVERTERS: dict[str, tuple[Any, Any]] = {
    "on_off_transition_time": (_tenths_to_s, _s_to_tenths),
    "on_transition_time": (_tenths_to_s, _s_to_tenths),
    "off_transition_time": (_tenths_to_s, _s_to_tenths),
    "on_level": (_null_is_255, _255_is_null),
    "power_on_level": (_null_is_255, _255_is_null),
    "power_on_color_temperature": (_mireds_to_k, _k_to_mireds),
}

# OnOff StartUpOnOff: null means "whatever it was before the power cut".
STARTUP_ON_OFF = {0: "off", 1: "on", 2: "toggle", None: "previous"}


# -- sensors -----------------------------------------------------------------
# (cluster, attribute) -> converter. Only what the attribute plainly means; an
# attribute not listed here is published raw.

def _scaled(factor: float, digits: int = 2) -> Any:
    return lambda v: None if v is None else round(v * factor, digits)


def _lux(v: Any) -> Any:
    if v is None or v == 0:
        return None if v is None else 0
    return round(10 ** ((v - 1) / 10000), 1)


SENSOR_CONVERTERS: dict[tuple[int, int], Any] = {
    (1026, 0): _scaled(0.01),        # TemperatureMeasurement, 0.01 C
    (1029, 0): _scaled(0.01),        # RelativeHumidityMeasurement, 0.01 %
    (1027, 0): _scaled(1, 1),        # PressureMeasurement, kPa x10 = hPa
    (1024, 0): _lux,                 # IlluminanceMeasurement, log scale
    (144, 4): _scaled(0.001, 3),     # ElectricalPowerMeasurement Voltage, mV
    (144, 5): _scaled(0.001, 3),     # ActiveCurrent, mA
    (144, 8): _scaled(0.001, 3),     # ActivePower, mW
    (47, 12): _scaled(0.5, 1),       # PowerSource BatPercentRemaining, half percent
    (WIFI_DIAGNOSTICS, 4): lambda v: v,  # RSSI, dBm
    (GENERAL_DIAGNOSTICS, 1): lambda v: v,  # RebootCount
}

BINARY_SENSOR_CONVERTERS: dict[tuple[int, int], Any] = {
    (1030, 0): lambda v: None if v is None else bool(int(v) & 1),  # Occupancy bitmap
    (69, 0): lambda v: None if v is None else not v,  # BooleanState: contact closed -> door "off"
}


class MatterDriver(Driver):
    platform = "matter"
    # Push-only: the server streams attribute changes over the socket, and the
    # driver runs its own reconnect loop.
    poll_interval = None

    def __init__(self, hub: Any, entry: Any) -> None:
        super().__init__(hub, entry)
        self.url = self._server_url()
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._keeper: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._closing: asyncio.Future | None = None
        self.server_info: dict[str, Any] = {}
        self.nodes: dict[int, dict[str, Any]] = {}
        self._refs: dict[str, MatterRef] = {}
        self._by_node: dict[int, list[EntityEntry]] = {}
        #: node_id -> {unique_id: spec}, from the node's own clusters (``node_entity_specs``).
        self._specs: dict[int, dict[str, dict[str, Any]]] = {}
        self._index_entities()

    # -- configuration -----------------------------------------------------
    def _server_url(self) -> str:
        """Where the Matter Server is.

        HA stored the add-on's internal name (``ws://core-matter-server:5580/ws``),
        which resolves nowhere outside the Supervisor network, so it is replaced:
        ``HOMEHUB_MATTER_URL`` wins, then an ``url`` option on the entry, then the
        stored URL with that one host name swapped for localhost (the server runs
        with host networking on the hub's host).
        """
        explicit = os.environ.get("HOMEHUB_MATTER_URL") or self.entry.options.get("url")
        if explicit:
            return str(explicit)
        stored = str(self.entry.data.get("url") or "ws://localhost:5580/ws")
        parsed = urlparse(stored)
        if parsed.hostname == "core-matter-server":
            netloc = "localhost" + (f":{parsed.port}" if parsed.port else "")
            return urlunparse(parsed._replace(netloc=netloc))
        return stored

    def _index_entities(self) -> None:
        self._refs.clear()
        self._by_node.clear()
        for e in self.entities:
            ref = parse_unique_id(e.unique_id)
            if ref is None:
                self.log.warning("%s: unique_id %r is not a Matter one, skipped", e.entity_id, e.unique_id)
                continue
            self._refs[e.entity_id] = ref
            self._by_node.setdefault(ref.node_id, []).append(e)

    def _expected_fabric(self) -> int | None:
        fabrics = {r.fabric for r in self._refs.values()}
        return fabrics.pop() if len(fabrics) == 1 else None

    # -- lifecycle -----------------------------------------------------------
    async def async_setup(self) -> None:
        await self._connect()
        self._keeper = asyncio.create_task(self._keep_connected(), name="matter-keeper")

    async def async_unload(self) -> None:
        for task in (self._keeper, self._reader):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._keeper = self._reader = None
        await self._close()

    async def _close(self) -> None:
        ws, session = self._ws, self._session
        self._ws = self._session = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()
        self._fail_pending(DeviceUnavailable("Matter Server connection closed"))

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _connect(self) -> None:
        """Open the socket, check the fabric, load every node and publish."""
        await self._close()
        session = aiohttp.ClientSession()
        try:
            ws = await asyncio.wait_for(session.ws_connect(self.url, heartbeat=55, max_msg_size=0), timeout=15)
            first = await asyncio.wait_for(ws.receive(), timeout=15)
            if first.type != aiohttp.WSMsgType.TEXT:
                raise ConnectionError(f"Matter Server sent no server_info ({first.type.name})")
            info = json.loads(first.data)
        except BaseException:
            await session.close()
            raise
        self._session, self._ws = session, ws
        self.server_info = info if isinstance(info, dict) else {}
        expected = self._expected_fabric()
        got = self.server_info.get("compressed_fabric_id")
        if expected is not None and got is not None and int(got) != expected:
            # A server started without the copied storage (or without
            # --fabricid 2 --vendorid 4939) makes a new fabric; none of our
            # devices are on it and every entity would sit unavailable.
            await self._close()
            raise ConnectionError(
                f"Matter Server fabric {int(got):016X} is not the imported fabric {expected:016X}; "
                "start it on a copy of the add-on's storage (see MATTER.md)")
        self._reader = asyncio.create_task(self._read(ws), name="matter-reader")
        nodes = await self._send("start_listening", timeout=LISTEN_TIMEOUT)
        self.nodes = {int(n["node_id"]): n for n in nodes or [] if isinstance(n, dict)}
        self._specs.clear()
        self.log.info("Matter Server %s: fabric %s, schema %s, %d nodes", self.url,
                      f"{int(got):016X}" if got is not None else "?", self.server_info.get("schema_version"),
                      len(self.nodes))
        self._publish_all()

    async def _keep_connected(self) -> None:
        """Wait for the socket to drop, mark everything unavailable, reconnect with backoff."""
        attempt = 0
        while True:
            if self._reader is not None:
                # asyncio.wait, not ``await reader``: suppressing the reader's
                # CancelledError here would also swallow our own cancellation
                # at unload, and the keeper would reconnect forever.
                await asyncio.wait({self._reader})
            self.log.warning("lost the Matter Server at %s", self.url)
            await self._close()
            self.mark_unavailable()
            while True:
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                attempt += 1
                await asyncio.sleep(delay)
                try:
                    await self._connect()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.log.warning("Matter Server reconnect failed (retry in %ss): %s",
                                     RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)], exc)
                    continue
                attempt = 0
                break

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        self.log.warning("Matter Server sent a frame that is not JSON")
                        continue
                    try:
                        self._handle(data)
                    except Exception:
                        self.log.exception("handling a Matter Server message failed")
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE,
                                  aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    break
        finally:
            self._fail_pending(DeviceUnavailable("Matter Server connection closed"))

    # -- protocol ------------------------------------------------------------
    async def _send(self, command: str, *, timeout: float = COMMAND_TIMEOUT, **args: Any) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise DeviceUnavailable("not connected to the Matter Server")
        message_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = fut
        msg: dict[str, Any] = {"message_id": message_id, "command": command}
        if args:
            msg["args"] = args
        try:
            await ws.send_str(json.dumps(msg))
            reply = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            raise DeviceUnavailable(f"Matter Server did not answer {command} in {timeout:.0f}s") from exc
        except (aiohttp.ClientError, ConnectionError) as exc:
            raise DeviceUnavailable(f"Matter Server connection failed: {exc}") from exc
        finally:
            self._pending.pop(message_id, None)
        if "error_code" in reply:
            code = int(reply.get("error_code") or 0)
            details = reply.get("details") or ""
            if code in UNREACHABLE_ERRORS:
                raise DeviceUnavailable(f"{command}: {UNREACHABLE_ERRORS[code]} {details}".strip())
            raise HubError(f"{command} failed (Matter error {code}): {details}".strip())
        return reply.get("result")

    def _handle(self, msg: dict[str, Any]) -> None:
        if "message_id" in msg and ("result" in msg or "error_code" in msg):
            fut = self._pending.get(str(msg["message_id"]))
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        event = msg.get("event")
        if event is None:
            return
        data = msg.get("data")
        if event == "attribute_updated" and isinstance(data, list) and len(data) == 3:
            node_id, path, value = int(data[0]), str(data[1]), data[2]
            node = self.nodes.get(node_id)
            if node is None:
                return
            attrs = node.setdefault("attributes", {})
            if value is None and path not in self._nullable_paths(node_id):
                attrs.pop(path, None)  # matter.js sends null for a removed attribute
            else:
                attrs[path] = value
            self._publish_node(node_id)
        elif event in ("node_added", "node_updated") and isinstance(data, dict):
            node_id = int(data["node_id"])
            self.nodes[node_id] = data
            self._specs.pop(node_id, None)
            self._publish_node(node_id)
        elif event == "node_removed":
            node_id = int(data)
            self.nodes.pop(node_id, None)
            self._publish_node(node_id)
        elif event == "server_shutdown":
            # Closing ends the reader, and the keeper reconnects once the server is back.
            ws = self._ws
            if ws is not None:
                self._closing = asyncio.ensure_future(ws.close())

    def _nullable_paths(self, node_id: int) -> set[str]:
        """Attributes our entities read where null is a real value (StartUpOnOff and friends)."""
        return {self._refs[e.entity_id].path() for e in self._by_node.get(node_id, ())}

    async def _command(self, ref: MatterRef, cluster: int, command: str, payload: dict[str, Any] | None = None,
                       endpoint: int | None = None) -> Any:
        self._require_available(ref)
        return await self._send("device_command", node_id=ref.node_id,
                                endpoint_id=ref.endpoint if endpoint is None else endpoint,
                                cluster_id=cluster, command_name=command, payload=payload or {})

    async def _write(self, ref: MatterRef, value: Any) -> None:
        self._require_available(ref)
        result = await self._send("write_attribute", node_id=ref.node_id, attribute_path=ref.path(), value=value)
        for item in result if isinstance(result, list) else []:
            status = item.get("Status", item.get("status", 0)) if isinstance(item, dict) else 0
            if status:
                raise HubError(f"writing {ref.path()} on node {ref.node_id} failed: status {status}")
        node = self.nodes.get(ref.node_id)
        if node is not None:
            node.setdefault("attributes", {})[ref.path()] = value

    async def _read_back(self, ref: MatterRef, paths: list[str]) -> None:
        """Read the attributes a command changed and publish them, so the reply reflects the device."""
        try:
            values = await self._send("read_attribute", node_id=ref.node_id, attribute_path=paths, timeout=10)
        except HubError as exc:
            self.log.debug("read back of node %s failed: %s", ref.node_id, exc)
            return
        node = self.nodes.get(ref.node_id)
        if node is not None and isinstance(values, dict):
            node.setdefault("attributes", {}).update(values)
        self._publish_node(ref.node_id)

    def _require_available(self, ref: MatterRef) -> None:
        node = self.nodes.get(ref.node_id)
        if node is None:
            raise DeviceUnavailable(f"Matter node {ref.node_id} is not on the server")
        if not node.get("available", False):
            raise DeviceUnavailable(f"Matter node {ref.node_id} is unreachable")

    # -- state -----------------------------------------------------------------
    def _attrs(self, node_id: int) -> dict[str, Any]:
        node = self.nodes.get(node_id) or {}
        return node.get("attributes") or {}

    def _publish_all(self) -> None:
        for node_id in list(self._by_node):
            self._publish_node(node_id)

    def _node_specs(self, node_id: int) -> dict[str, dict[str, Any]]:
        """``unique_id -> spec`` for one node, computed from its clusters (cached until the node changes)."""
        cached = self._specs.get(node_id)
        if cached is not None:
            return cached
        node = self.nodes.get(node_id)
        ents = self._by_node.get(node_id) or []
        if node is None or not ents:
            return {}
        fabric = self._refs[ents[0].entity_id].fabric
        specs = {str(sp["unique_id"]).upper(): sp for sp in node_entity_specs(fabric, node)}
        self._specs[node_id] = specs
        return specs

    def _spec(self, entity: EntityEntry, ref: MatterRef) -> dict[str, Any]:
        return self._node_specs(ref.node_id).get(str(entity.unique_id).upper()) or {}

    def _confirm_node(self, node_id: int, node: dict[str, Any]) -> None:
        """What the node says about itself and its entities, written to the registry as confirmed."""
        ents = self._by_node.get(node_id) or []
        if not ents:
            return
        fabric = self._refs[ents[0].entity_id].fabric
        info = node_device_info(fabric, node)
        info.pop("name", None)
        for dev_id in {e.device_id for e in ents if e.device_id}:
            self.confirm_device(dev_id, **info)
        for entity in ents:
            spec = self._spec(entity, self._refs[entity.entity_id])
            fields = {k: spec[k] for k in ("original_device_class", "unit_of_measurement", "capabilities",
                                           "supported_features", "entity_category") if k in spec}
            if spec.get("name"):
                fields["original_name"] = spec["name"]
            if fields:
                self.confirm_entity(entity, **fields)

    def _publish_node(self, node_id: int) -> None:
        node = self.nodes.get(node_id)
        if node is not None and node.get("available", False):
            try:
                self._confirm_node(node_id, node)
            except Exception:
                self.log.exception("reading node %s's own description failed", node_id)
        for entity in self._by_node.get(node_id, ()):
            if node is None or not node.get("available", False):
                cur = self.hub.states.get(entity.entity_id)
                self.publish(entity, UNAVAILABLE, dict(cur.attributes) if cur else {})
                continue
            try:
                self._publish_entity(entity, self._refs[entity.entity_id], node.get("attributes") or {})
            except Exception:
                self.log.exception("publishing %s failed", entity.entity_id)

    def _publish_entity(self, entity: EntityEntry, ref: MatterRef, a: dict[str, Any]) -> None:
        domain = entity.domain
        spec = self._spec(entity, ref)
        if domain == "light":
            state, attrs = self._light_state(entity, ref, a)
            self.publish(entity, state, attrs)
        elif domain == "number":
            to_ha = NUMBER_CONVERTERS.get(ref.key, (lambda v: v, None))[0]
            value = to_ha(a.get(ref.path()))
            caps = spec.get("capabilities") or {}
            attrs = {k: caps[k] for k in ("min", "max", "step", "mode") if k in caps}
            if spec.get("unit_of_measurement"):
                attrs["unit_of_measurement"] = spec["unit_of_measurement"]
            self.publish(entity, UNKNOWN if value is None else _num_str(value), attrs)
        elif domain == "select":
            options = list((spec.get("capabilities") or {}).get("options") or STARTUP_ON_OFF.values())
            raw = a.get(ref.path())
            value = STARTUP_ON_OFF.get(raw) if ref.key == "MatterStartUpOnOff" else raw
            self.publish(entity, UNKNOWN if value is None else str(value), {"options": options})
        elif domain == "button":
            # A button's state is when it was last pressed; keep that across reconnects.
            cur = self.hub.states.get(entity.entity_id)
            keep = cur.state if cur is not None and cur.state not in (UNAVAILABLE, "") else UNKNOWN
            self.publish(entity, keep, _classed(spec))
        elif domain == "update":
            version = a.get(f"{ref.endpoint}/{BASIC_INFORMATION}/10") or a.get(ref.path())
            self.publish(entity, "off", {
                "installed_version": None if version is None else str(version),
                "latest_version": None if version is None else str(version),
                "auto_update": False, "in_progress": False, "skipped_version": None,
                "release_summary": None, "release_url": None, "title": None,
                "supported_features": UPDATE_FEATURES,
            })
        elif domain == "sensor":
            self.publish(entity, *self._sensor_state(entity, ref, a))
        elif domain == "switch":
            raw = a.get(ref.path())
            self.publish(entity, UNKNOWN if raw is None else ("on" if raw else "off"), _classed(spec))
        elif domain == "binary_sensor":
            conv = BINARY_SENSOR_CONVERTERS.get((ref.cluster, ref.attribute), lambda v: None if v is None else bool(v))
            value = conv(a.get(ref.path()))
            self.publish(entity, UNKNOWN if value is None else ("on" if value else "off"), _classed(spec))
        else:
            raw = a.get(ref.path())
            self.publish(entity, UNKNOWN if raw is None else str(raw), {})

    def _sensor_state(self, entity: EntityEntry, ref: MatterRef, a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        spec = self._spec(entity, ref)
        caps = spec.get("capabilities") or {}
        attrs: dict[str, Any] = _classed(spec)
        if spec.get("unit_of_measurement"):
            attrs["unit_of_measurement"] = spec["unit_of_measurement"]
        if caps.get("state_class"):
            attrs["state_class"] = caps["state_class"]
        raw = a.get(ref.path())
        if (ref.cluster, ref.attribute) == (GENERAL_DIAGNOSTICS, 2):
            # UpTime in seconds; HA shows it as the boot time. Rounded to the
            # minute so each uptime tick is not a state change.
            if raw is None:
                return UNKNOWN, attrs
            boot = (datetime.now(UTC) - timedelta(seconds=int(raw))).replace(second=0, microsecond=0)
            return boot.isoformat(), attrs
        conv = SENSOR_CONVERTERS.get((ref.cluster, ref.attribute), lambda v: v)
        value = conv(raw)
        return (UNKNOWN if value is None else _num_str(value)), attrs

    def _light_state(self, entity: EntityEntry, ref: MatterRef, a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        ep = ref.endpoint
        modes = self._color_modes(entity, ep, a)
        min_k, max_k = self._kelvin_range(entity, ep, a)
        attrs: dict[str, Any] = {
            "supported_color_modes": modes,
            "supported_features": LIGHT_FEATURE_TRANSITION,
            "color_mode": None, "brightness": None,
        }
        if "color_temp" in modes:
            attrs.update({"min_color_temp_kelvin": min_k, "max_color_temp_kelvin": max_k,
                          "min_mireds": kelvin_to_mired(max_k), "max_mireds": kelvin_to_mired(min_k),
                          "color_temp_kelvin": None, "color_temp": None})
        if any(m in modes for m in ("hs", "xy", "color_temp")):
            attrs.update({"hs_color": None, "rgb_color": None, "xy_color": None})
        on = a.get(f"{ep}/{ON_OFF}/0")
        if on is None:
            return UNKNOWN, attrs
        if not on:
            return "off", attrs
        if modes != ["onoff"]:
            level = a.get(f"{ep}/{LEVEL_CONTROL}/0")
            if level is not None:
                lo, hi = self._level_range(ep, a)
                attrs["brightness"] = max(1, min(255, round(_renormalize(level, (lo, hi), (1, 255)))))
        color_mode = self._current_color_mode(modes, ep, a)
        attrs["color_mode"] = color_mode
        rgb: tuple[int, int, int] | None = None
        if color_mode == "color_temp":
            mireds = a.get(f"{ep}/{COLOR_CONTROL}/7")
            if mireds:
                kelvin = mired_to_kelvin(mireds)
                attrs["color_temp_kelvin"] = kelvin
                attrs["color_temp"] = int(mireds)
                rgb = kelvin_to_rgb(kelvin)
        elif color_mode == "hs":
            hue, sat = a.get(f"{ep}/{COLOR_CONTROL}/0"), a.get(f"{ep}/{COLOR_CONTROL}/1")
            if hue is not None and sat is not None:
                hs = (round(_renormalize(hue, (0, 254), (0, 360)), 3), round(_renormalize(sat, (0, 254), (0, 100)), 3))
                attrs["hs_color"] = hs
                rgb = hs_to_rgb(hs)
        elif color_mode == "xy":
            x, y = a.get(f"{ep}/{COLOR_CONTROL}/3"), a.get(f"{ep}/{COLOR_CONTROL}/4")
            if x is not None and y is not None:
                xy = (round(x / 65536, 4), round(y / 65536, 4))
                attrs["xy_color"] = xy
                rgb = xy_to_rgb(*xy)
        if rgb is not None and "rgb_color" in attrs:
            attrs["rgb_color"] = rgb
            if attrs.get("hs_color") is None:
                attrs["hs_color"] = rgb_to_hs(rgb)
            if attrs.get("xy_color") is None:
                attrs["xy_color"] = rgb_to_xy(rgb)
        return "on", attrs

    def _color_modes(self, entity: EntityEntry, ep: int, a: dict[str, Any]) -> list[str]:
        features = a.get(f"{ep}/{COLOR_CONTROL}/65532")
        if features is not None:
            modes = [m for bit, m in ((CC_FEATURE_CT, "color_temp"), (CC_FEATURE_HS, "hs"), (CC_FEATURE_XY, "xy"))
                     if int(features) & bit]
            if modes:
                return modes
        if not any(k.startswith(f"{ep}/{COLOR_CONTROL}/") for k in a):
            # No ColorControl on the node: dimmable or plain on/off.
            if any(k.startswith(f"{ep}/{LEVEL_CONTROL}/") for k in a):
                return ["brightness"]
            if any(k.startswith(f"{ep}/{ON_OFF}/") for k in a):
                return ["onoff"]
        # The node did not say: the modes its clusters imply (never an imported registry value).
        ref = self._refs.get(entity.entity_id)
        spec = self._spec(entity, ref) if ref is not None else {}
        return list((spec.get("capabilities") or {}).get("supported_color_modes") or ["onoff"])

    def _kelvin_range(self, entity: EntityEntry, ep: int, a: dict[str, Any]) -> tuple[int, int]:
        ref = self._refs.get(entity.entity_id)
        caps = (self._spec(entity, ref) if ref is not None else {}).get("capabilities") or {}
        lo_m = a.get(f"{ep}/{COLOR_CONTROL}/16395")
        hi_m = a.get(f"{ep}/{COLOR_CONTROL}/16396")
        min_k = mired_to_kelvin(hi_m) if hi_m else caps.get("min_color_temp_kelvin") or mired_to_kelvin(DEFAULT_MAX_MIREDS)
        max_k = mired_to_kelvin(lo_m) if lo_m else caps.get("max_color_temp_kelvin") or mired_to_kelvin(DEFAULT_MIN_MIREDS)
        return int(min_k), int(max_k)

    @staticmethod
    def _level_range(ep: int, a: dict[str, Any]) -> tuple[int, int]:
        lo = a.get(f"{ep}/{LEVEL_CONTROL}/2") or DEFAULT_MIN_LEVEL
        hi = a.get(f"{ep}/{LEVEL_CONTROL}/3") or DEFAULT_MAX_LEVEL
        return (int(lo), int(hi)) if hi > lo else (DEFAULT_MIN_LEVEL, DEFAULT_MAX_LEVEL)

    @staticmethod
    def _current_color_mode(modes: list[str], ep: int, a: dict[str, Any]) -> str:
        mode = CC_MODE.get(a.get(f"{ep}/{COLOR_CONTROL}/8"))
        if mode in modes:
            return mode
        if mode == "xy" and "hs" in modes:
            return "hs"
        return modes[0]

    # -- services --------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        ref = self._refs.get(entity.entity_id)
        if ref is None:
            raise ServiceNotFound(f"{entity.entity_id} has no Matter unique_id")
        handler = {
            ("light", "turn_on"): self._light_on, ("light", "turn_off"): self._light_off,
            ("light", "toggle"): self._light_toggle, ("number", "set_value"): self._number_set,
            ("select", "select_option"): self._select_option, ("button", "press"): self._press,
            ("switch", "turn_on"): self._switch_on, ("switch", "turn_off"): self._switch_off,
            ("switch", "toggle"): self._switch_toggle,
        }.get((domain, service))
        if handler is None or (domain in ("light", "switch") and entity.domain != domain):
            raise ServiceNotFound(f"matter cannot run {domain}.{service} on {entity.entity_id}")
        return await handler(entity, ref, data)

    def _is_on(self, ref: MatterRef) -> bool:
        return bool(self._attrs(ref.node_id).get(f"{ref.endpoint}/{ON_OFF}/0"))

    async def _light_toggle(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        if self._is_on(ref):
            await self._light_off(entity, ref, {k: v for k, v in data.items() if k == "transition"})
        else:
            await self._light_on(entity, ref, data)

    async def _light_off(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        transition = data.get("transition")
        if transition:
            # Off with a fade: Home Assistant does it as a level move to zero.
            await self._command(ref, LEVEL_CONTROL, "MoveToLevelWithOnOff",
                                {"level": 0, "transitionTime": _tenths(transition),
                                 "optionsMask": 0, "optionsOverride": 0})
        else:
            await self._command(ref, ON_OFF, "Off")
        await self._read_back(ref, [f"{ref.endpoint}/{ON_OFF}/0", f"{ref.endpoint}/{LEVEL_CONTROL}/0"])

    async def _light_on(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        ep = ref.endpoint
        a = self._attrs(ref.node_id)
        modes = self._color_modes(entity, ep, a)
        transition = _tenths(data.get("transition") or 0)

        brightness = _target_brightness(data, self.hub.states.get(entity.entity_id))
        if brightness is not None and brightness <= 0:
            await self._light_off(entity, ref, data)
            return

        color_cmd = self._color_command(entity, ep, a, modes, data, transition)
        if color_cmd is not None:
            await self._command(ref, COLOR_CONTROL, *color_cmd)

        if brightness is not None and modes != ["onoff"]:
            lo, hi = self._level_range(ep, a)
            level = max(lo, min(hi, round(_renormalize(brightness, (0, 255), (lo, hi)))))
            await self._command(ref, LEVEL_CONTROL, "MoveToLevelWithOnOff",
                                {"level": level, "transitionTime": transition,
                                 "optionsMask": 0, "optionsOverride": 0})
        else:
            await self._command(ref, ON_OFF, "On")
        paths = [f"{ep}/{ON_OFF}/0", f"{ep}/{LEVEL_CONTROL}/0"]
        if color_cmd is not None:
            paths += [f"{ep}/{COLOR_CONTROL}/{n}" for n in (0, 1, 3, 4, 7, 8)]
        await self._read_back(ref, paths)

    def _color_command(self, entity: EntityEntry, ep: int, a: dict[str, Any], modes: list[str],
                       data: dict[str, Any], transition: int) -> tuple[str, dict[str, Any]] | None:
        opts = {"transitionTime": transition, "optionsMask": EXECUTE_IF_OFF, "optionsOverride": EXECUTE_IF_OFF}
        kelvin = data.get("color_temp_kelvin", data.get("kelvin"))
        if kelvin is None and data.get("color_temp") is not None:  # legacy mireds
            kelvin = mired_to_kelvin(float(data["color_temp"]))
        if kelvin is not None:
            if "color_temp" not in modes:
                if "hs" not in modes and "xy" not in modes:
                    return None
                data = {**data, "rgb_color": kelvin_to_rgb(float(kelvin))}
            else:
                min_k, max_k = self._kelvin_range(entity, ep, a)
                k = max(min_k, min(max_k, float(kelvin)))
                return "MoveToColorTemperature", {"colorTemperatureMireds": kelvin_to_mired(k), **opts}

        hs: tuple[float, float] | None = None
        xy: tuple[float, float] | None = None
        if data.get("hs_color") is not None:
            hs = (float(data["hs_color"][0]), float(data["hs_color"][1]))
        elif data.get("xy_color") is not None:
            xy = (float(data["xy_color"][0]), float(data["xy_color"][1]))
        elif data.get("rgb_color") is not None:
            rgb = tuple(float(c) for c in data["rgb_color"][:3])
            if "xy" in modes:
                xy = rgb_to_xy(rgb)  # type: ignore[arg-type]
            else:
                hs = rgb_to_hs(rgb)  # type: ignore[arg-type]
        else:
            return None
        if hs is not None and "hs" not in modes and "xy" in modes:
            xy = rgb_to_xy(hs_to_rgb(hs))
            hs = None
        if xy is not None and "xy" not in modes and "hs" in modes:
            hs = rgb_to_hs(xy_to_rgb(*xy))
            xy = None
        if hs is not None and "hs" in modes:
            return "MoveToHueAndSaturation", {
                "hue": max(0, min(254, round(hs[0] / 360 * 254))),
                "saturation": max(0, min(254, round(hs[1] / 100 * 254))), **opts}
        if xy is not None and "xy" in modes:
            # 0xFEFF is the highest legal CurrentX/CurrentY.
            return "MoveToColor", {"colorX": max(0, min(0xFEFF, int(xy[0] * 65536))),
                                   "colorY": max(0, min(0xFEFF, int(xy[1] * 65536))), **opts}
        self.log.info("%s cannot show that colour (modes %s); ignored", entity.entity_id, modes)
        return None

    async def _number_set(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        if "value" not in data:
            raise HubError("number.set_value needs a value")
        value = float(data["value"])
        caps = self._spec(entity, ref).get("capabilities") or {}
        if ("min" in caps and value < caps["min"]) or ("max" in caps and value > caps["max"]):
            raise HubError(f"{value} is outside {caps.get('min')}..{caps.get('max')} for {entity.entity_id}")
        to_device = NUMBER_CONVERTERS.get(ref.key, (None, lambda v: int(v) if float(v).is_integer() else v))[1]
        await self._write(ref, to_device(value))
        self._publish_node(ref.node_id)

    async def _select_option(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        option = data.get("option")
        if ref.key != "MatterStartUpOnOff":
            raise ServiceNotFound(f"{entity.entity_id} ({ref.key}) is read-only here")
        by_option = {v: k for k, v in STARTUP_ON_OFF.items()}
        if option not in by_option:
            raise HubError(f"{option!r} is not one of {list(by_option)}")
        await self._write(ref, by_option[option])
        self._publish_node(ref.node_id)

    async def _press(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        if ref.cluster != IDENTIFY:
            raise ServiceNotFound(f"{entity.entity_id} ({ref.key}) has no press action here")
        await self._command(ref, IDENTIFY, "Identify", {"identifyTime": IDENTIFY_SECONDS})
        self.publish(entity, datetime.now(UTC).isoformat(), {})

    # A plug's OnOff (HA's ``MatterPlug`` switch): the command, then read the state back.
    async def _switch_on(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        await self._command(ref, ON_OFF, "On")
        await self._read_back(ref, [ref.path()])

    async def _switch_off(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        await self._command(ref, ON_OFF, "Off")
        await self._read_back(ref, [ref.path()])

    async def _switch_toggle(self, entity: EntityEntry, ref: MatterRef, data: dict[str, Any]) -> None:
        await self._command(ref, ON_OFF, "Toggle")
        await self._read_back(ref, [ref.path()])

    # -- adding devices (services/homehub/flows.py) ------------------------------
    async def commission_with_code(self, code: str, *, network_only: bool = True,
                                   timeout: float = COMMISSION_TIMEOUT) -> dict[str, Any]:
        """Commission a new device onto our fabric with its setup code; returns the new node.

        ``network_only`` is for a device already on the LAN (Wi-Fi or Ethernet,
        in pairing mode). The server answers once the device has joined and
        been interviewed, which can take a minute or two.
        """
        node = await self._send("commission_with_code", timeout=timeout, code=code, network_only=network_only)
        if not isinstance(node, dict) or "node_id" not in node:
            raise HubError("the Matter Server did not return the new node")
        self.nodes[int(node["node_id"])] = node
        return node

    @property
    def compressed_fabric_id(self) -> int | None:
        got = self.server_info.get("compressed_fabric_id")
        return int(got) if got is not None else self._expected_fabric()

    def reindex(self, node_id: int | None = None) -> None:
        """Pick up registry entities added while running, and publish them (or one node's)."""
        self._index_entities()
        if node_id is None:
            self._publish_all()
        else:
            self._publish_node(int(node_id))


def _classed(spec: dict[str, Any]) -> dict[str, Any]:
    """The device class the node's clusters give an entity, as a state attribute."""
    return {"device_class": spec["original_device_class"]} if spec.get("original_device_class") else {}


def _tenths(seconds: Any) -> int:
    return max(0, min(65534, round(float(seconds or 0) * 10)))


def _num_str(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value) if abs(value) < 1e15 else value
    return str(value)


def _target_brightness(data: dict[str, Any], current: Any) -> float | None:
    """HA's brightness keys, absolute or stepped, as 0-255 (None = leave it alone)."""
    if data.get("brightness") is not None:
        return float(data["brightness"])
    if data.get("brightness_pct") is not None:
        return float(data["brightness_pct"]) * 255 / 100
    step = None
    if data.get("brightness_step") is not None:
        step = float(data["brightness_step"])
    elif data.get("brightness_step_pct") is not None:
        step = float(data["brightness_step_pct"]) * 255 / 100
    if step is None:
        return None
    base = 0.0
    if current is not None and current.state == "on":
        base = float(current.attributes.get("brightness") or 0)
    return max(0.0, min(255.0, base + step))


# -- registry entries for a newly commissioned node ---------------------------
# The imported registry has entities for the nodes HA commissioned; a node added
# later gets its entities here, with HA's unique_id scheme so the driver above
# serves them like the imported ones.

DESCRIPTOR = 29
OTA_REQUESTOR = 42
#: Matter device types that are plugs, not lights (On/Off and Dimmable Plug-in Unit).
PLUG_DEVICE_TYPES = {0x010A, 0x010B}
#: Light device types (On/Off, Dimmable, Color Temperature, Extended Color).
LIGHT_DEVICE_TYPES = {0x0100, 0x0101, 0x010C, 0x010D}

#: (cluster, attribute) -> (domain, key, name, extra registry fields), per endpoint.
_ENDPOINT_ENTITIES: list[tuple[int, int, str, str, str, dict[str, Any]]] = [
    (LEVEL_CONTROL, 17, "number", "on_level", "On level",
     {"capabilities": {"min": 0, "max": 255, "step": 1, "mode": "box"}, "entity_category": "config"}),
    (LEVEL_CONTROL, 18, "number", "on_transition_time", "On transition time",
     {"capabilities": {"min": 0, "max": 65534, "step": 0.1, "mode": "box"}, "unit_of_measurement": "s",
      "entity_category": "config"}),
    (LEVEL_CONTROL, 19, "number", "off_transition_time", "Off transition time",
     {"capabilities": {"min": 0, "max": 65534, "step": 0.1, "mode": "box"}, "unit_of_measurement": "s",
      "entity_category": "config"}),
    (LEVEL_CONTROL, 16384, "number", "power_on_level", "Power-on level",
     {"capabilities": {"min": 0, "max": 255, "step": 1, "mode": "box"}, "entity_category": "config"}),
    (1026, 0, "sensor", "TemperatureSensor", "Temperature",
     {"original_device_class": "temperature", "unit_of_measurement": "°C",
      "capabilities": {"state_class": "measurement"}}),
    (1029, 0, "sensor", "HumiditySensor", "Humidity",
     {"original_device_class": "humidity", "unit_of_measurement": "%", "capabilities": {"state_class": "measurement"}}),
    (1027, 0, "sensor", "PressureSensor", "Pressure",
     {"original_device_class": "pressure", "unit_of_measurement": "hPa",
      "capabilities": {"state_class": "measurement"}}),
    (1024, 0, "sensor", "LightSensor", "Illuminance",
     {"original_device_class": "illuminance", "unit_of_measurement": "lx",
      "capabilities": {"state_class": "measurement"}}),
    (144, 8, "sensor", "ElectricalPowerMeasurementWatt", "Power",
     {"original_device_class": "power", "unit_of_measurement": "W", "capabilities": {"state_class": "measurement"}}),
    (47, 12, "sensor", "PowerSource", "Battery",
     {"original_device_class": "battery", "unit_of_measurement": "%", "entity_category": "diagnostic",
      "capabilities": {"state_class": "measurement"}}),
    (1030, 0, "binary_sensor", "OccupancySensor", "Occupancy", {"original_device_class": "occupancy"}),
    (69, 0, "binary_sensor", "ContactSensor", "Door", {"original_device_class": "door"}),
]
#: Node-wide diagnostics on endpoint 0.
_ROOT_ENTITIES: list[tuple[int, int, str, str, str, dict[str, Any]]] = [
    (WIFI_DIAGNOSTICS, 4, "sensor", "WiFiDiagnosticsRssi", "Wi-Fi RSSI",
     {"original_device_class": "signal_strength", "unit_of_measurement": "dBm", "entity_category": "diagnostic",
      "capabilities": {"state_class": "measurement"}}),
    (GENERAL_DIAGNOSTICS, 1, "sensor", "GeneralDiagnosticsRebootCount", "Reboot count",
     {"entity_category": "diagnostic"}),
    (GENERAL_DIAGNOSTICS, 2, "sensor", "GeneralDiagnosticsUpTime", "Uptime",
     {"original_device_class": "timestamp", "entity_category": "diagnostic"}),
]


def _device_types(attrs: dict[str, Any], ep: int) -> set[int]:
    out: set[int] = set()
    for item in attrs.get(f"{ep}/{DESCRIPTOR}/0") or []:
        if isinstance(item, dict):
            value = item.get("0", item.get("deviceType"))
            if value is not None:
                with contextlib.suppress(TypeError, ValueError):
                    out.add(int(value))
    return out


def node_device_info(fabric: int, node: dict[str, Any]) -> dict[str, Any]:
    """The DeviceEntry fields HA's Matter integration gives a node (BasicInformation on endpoint 0)."""
    a = node.get("attributes") or {}
    node_id = int(node["node_id"])

    def basic(attr: int) -> Any:
        value = a.get(f"0/{BASIC_INFORMATION}/{attr}")
        return value if value not in ("", None) else None

    serial = basic(15)
    identifiers = [["matter", f"deviceid_{fabric:016X}-{node_id:016X}-MatterNodeDevice"]]
    if serial:
        identifiers.append(["matter", f"serial_{serial}"])
    return {
        "name": basic(5) or basic(3) or f"Matter device {node_id}",
        "manufacturer": basic(1),
        "model": basic(3),
        "sw_version": None if basic(10) is None else str(basic(10)),
        "hw_version": None if basic(8) is None else str(basic(8)),
        "serial_number": None if serial is None else str(serial),
        "identifiers": identifiers,
    }


def node_entity_specs(fabric: int, node: dict[str, Any]) -> list[dict[str, Any]]:
    """Registry entity specs for one node, in HA's unique_id scheme.

    Each spec has ``domain``, ``unique_id``, ``name`` (the entity's own name,
    ``None`` for the endpoint's main light or plug) and EntityEntry fields.
    """
    a = node.get("attributes") or {}
    node_id = int(node["node_id"])
    endpoints = sorted({int(k.split("/", 1)[0]) for k in a if k.split("/", 1)[0].isdigit()})
    prefix = f"{fabric:016X}-{node_id:016X}-MatterNodeDevice"
    specs: list[dict[str, Any]] = []

    def add(domain: str, ep: int, key: str, cluster: int, attribute: int, name: str | None, **extra: Any) -> None:
        specs.append({"domain": domain, "unique_id": f"{prefix}-{ep}-{key}-{cluster}-{attribute}",
                      "name": name, **extra})

    def has(ep: int, cluster: int, attribute: int | None = None) -> bool:
        if attribute is not None:
            return f"{ep}/{cluster}/{attribute}" in a
        return any(k.startswith(f"{ep}/{cluster}/") for k in a)

    for ep in endpoints:
        if ep == 0:
            continue
        types = _device_types(a, ep)
        if has(ep, ON_OFF, 0):
            is_plug = bool(types & PLUG_DEVICE_TYPES) or (
                not types & LIGHT_DEVICE_TYPES and not has(ep, LEVEL_CONTROL) and not has(ep, COLOR_CONTROL))
            if is_plug:
                add("switch", ep, "MatterPlug", ON_OFF, 0, None, original_device_class="outlet")
            else:
                modes = ["onoff"]
                features = a.get(f"{ep}/{COLOR_CONTROL}/65532")
                if features is not None:
                    modes = [m for bit, m in ((CC_FEATURE_CT, "color_temp"), (CC_FEATURE_HS, "hs"),
                                              (CC_FEATURE_XY, "xy")) if int(features) & bit] or ["onoff"]
                elif has(ep, LEVEL_CONTROL):
                    modes = ["brightness"]
                caps: dict[str, Any] = {"supported_color_modes": modes}
                if "color_temp" in modes:
                    lo_m = a.get(f"{ep}/{COLOR_CONTROL}/16395") or DEFAULT_MIN_MIREDS
                    hi_m = a.get(f"{ep}/{COLOR_CONTROL}/16396") or DEFAULT_MAX_MIREDS
                    caps.update({"min_color_temp_kelvin": mired_to_kelvin(hi_m),
                                 "max_color_temp_kelvin": mired_to_kelvin(lo_m)})
                add("light", ep, "MatterLight", ON_OFF, 0, None, capabilities=caps,
                    supported_features=LIGHT_FEATURE_TRANSITION)
            if has(ep, ON_OFF, 16387):
                add("select", ep, "MatterStartUpOnOff", ON_OFF, 16387, "Power-on behavior on startup",
                    capabilities={"options": ["on", "off", "toggle", "previous"]}, entity_category="config")
        if has(ep, IDENTIFY):
            add("button", ep, "IdentifyButton", IDENTIFY, 65529, "Identify",
                original_device_class="identify", entity_category="config")
        for cluster, attribute, domain, key, name, extra in _ENDPOINT_ENTITIES:
            if has(ep, cluster, attribute):
                add(domain, ep, key, cluster, attribute, name, **extra)
    for cluster, attribute, domain, key, name, extra in _ROOT_ENTITIES:
        if has(0, cluster, attribute):
            add(domain, 0, key, cluster, attribute, name, **extra)
    if has(0, BASIC_INFORMATION, 9) and any(has(ep, OTA_REQUESTOR) for ep in endpoints):
        add("update", 0, "MatterUpdate", BASIC_INFORMATION, 9, "Firmware", supported_features=UPDATE_FEATURES,
            entity_category="config")
    return specs
