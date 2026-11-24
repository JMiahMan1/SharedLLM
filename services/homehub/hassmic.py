"""echo-dot-assist ("hassmic") Echos, managed from their device page with no button, adb or settings page.

HomeHub is the authority of the household's Echo network. It makes the network key ``K`` (32 random bytes) and
the network id ``N`` once and keeps them sealed in its store (``Store.save_blob``, Fernet), never in a config file
or a log. For each adopted hassmic Echo it then:

1. learns the Echo's arbitration public key from its UDP beacon on port 28930 (one probe beacon with ``net = 0``
   makes every Echo answer at once) and, unless the Echo already beacons in network ``N`` with a tag made with
   ``K``, seals ``K`` to that key and hands it over with the node's ``arbitration_key`` action, over the keyed
   (Noise) ESPHome connection the driver already holds. The Echo takes it because ``N`` is lower than its own
   random network id (the lower id is the older network and wins);
2. logs in to the Echo's settings API (``http://<echo>:28931``) with a voucher it makes itself from ``K``
   (``/api/vouch/login``, labelled "HomeHub"). The Echo only checks the voucher's MAC, so no button is pressed;
3. signs every request with the per-Echo session key and a strictly increasing microsecond counter, and checks
   the MAC on every answer.

On top of that it offers, on the device page: a "Wake word engine" select (Amazon or microWakeWord, installing
ESPHome's official microWakeWord models first), an "Online updates" select (Off, Beta, Release; ``echo_ota.py``
finds it by its ``_online_updates`` entity id and turns it to Release on adoption), a "Wake word models"
diagnostic sensor, a "Microphone" problem sensor fed from the Echo's own log (with one automatic reboot an hour
and an admin notice when a reboot did not help) and a "Management" diagnostic sensor that says what HomeHub
could not do and why. Every failure is shown there with its reason; nothing falls back silently.

The protocol (byte layouts, labels, counter rules) is hassmic's own: ``arb.c`` (beacons, sealing), ``web.c``
(vouchers, session keys, signed requests), ``mww_store.c`` (wake word models). The crypto is byte for byte
what hassmic's Monocypher computes; ``tests/test_hassmic_crypto.py`` holds vectors made by hassmic's own C code.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from services.homehub.core import UNAVAILABLE, UNKNOWN, DeviceUnavailable, EntityEntry, HubError, ServiceNotFound

log = logging.getLogger("homehub.hassmic")

#: UDP port of hassmic's arbitration beacons (``main.c`` ``arb_port``).
ARB_PORT = 28930
#: TCP port of the Echo's settings API (``main.c`` ``core_web_port``).
WEB_PORT = 28931
#: Where the probe beacon goes: the broadcast address of the segment HomeHub (host network) is on.
BROADCAST_ADDR = "255.255.255.255"
#: The first network id HomeHub uses. A random 64-bit id falls below it with probability 2**-32, and every
#: rotation goes one lower (an Echo refuses an id that is not lower than its own), so there is room to rotate.
NET_ID_START = 0x00000000FFFFFFFF
#: The sealed store blob that holds K, N and HomeHub's two X25519 secret keys.
AUTHORITY_BLOB = "hassmic_authority"
#: What HomeHub calls itself in a voucher (``via``) and in the Echo's list of approved clients.
VIA = b"homehub"
LABEL = "HomeHub"
#: Seconds a probe waits for the Echos' answers (they answer at once, rate limited to one a second).
SCAN_S = 1.5
#: Seconds a fresh scan's beacons are reused by another Echo's bring-up.
SCAN_CACHE_S = 3.0
#: Seconds HomeHub waits for an Echo to beacon in network N after it was handed the key.
CONFIRM_S = 8.0
CONFIRM_POLL_S = 0.5
#: Seconds one settings API request may take (``/api/set online_updates`` probes GitHub for up to 15 s).
WEB_TIMEOUT_S = 10.0
SET_TIMEOUT_S = 35.0
UPLOAD_TIMEOUT_S = 60.0

#: ESPHome's official microWakeWord models (v2 manifests), fetched once and cached in HomeHub's data dir.
WAKE_MODELS_URL = "https://raw.githubusercontent.com/esphome/micro-wake-word-models/main/models/v2"
#: The models a hassmic Echo gets before it is switched to microWakeWord (entry option ``echo_wake_models``).
DEFAULT_WAKE_MODELS = ("hey_jarvis", "okay_nabu", "hey_mycroft", "alexa")
#: hassmic's model id rule (``mww_store.c``): 1-40 of ``[a-z0-9_-]``, starting with a letter or digit.
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MODEL_MAX_BYTES = 1 << 20
MANIFEST_MAX_BYTES = 8192

#: hassmic's own line when its processed microphone stream stopped (``audio_mixer.c`` ``cap_read``).
MIC_DEAD_LINE = "audio: micAsr delivers nothing"
#: The log lines looked at: a dead stream logs that line every few seconds, so the newest lines are full of it.
MIC_RECENT_LINES = 30
#: How many of those lines must be the dead-stream line (one is a hiccup the Echo recovers from by itself).
MIC_DEAD_LINES = 3
#: The newest dead-stream line must be this close to the end of the log (the stream is still dead now).
MIC_DEAD_TAIL = 6
#: At most one automatic reboot in this many seconds, per Echo.
REBOOT_EVERY_S = 3600.0
#: How long after a reboot a still dead microphone counts as "the reboot did not help".
REBOOT_SETTLE_S = 120.0

ENGINE_OPTIONS = {"amazon": "Amazon", "microwakeword": "microWakeWord"}
UPDATE_OPTIONS = {"off": "Off", "beta": "Beta", "release": "Release"}

ARBITRATION_OFF = ("This Echo is not running hassmic's arbitration (it offers no arbitration_key action). That "
                   "happens when its wake word engine is Home Assistant's (wake_engine=homeassistant) or when "
                   "arbitration is turned off in its hassmic.conf (-a 0 or -w remote). Without it HomeHub cannot "
                   "give it the Echo network key or log in to its settings. Switch its wake word engine back to "
                   "Amazon or microWakeWord and restart it; HomeHub then finishes the setup by itself.")
HA_ENGINE = ("The Echo uses Home Assistant's wake word engine (wake_engine=homeassistant), so its arbitration is "
             "off. Pick Amazon or microWakeWord here to turn it back on.")


class HassmicError(HubError):
    """Something HomeHub could not do for an Echo; the message is the reason shown on the device page."""


def is_hassmic(device_info: Any) -> bool:
    """Whether an ESPHome node is an echo-dot-assist (hassmic) Echo, from its own ``device_info``."""
    project = str(getattr(device_info, "project_name", "") or "").lower()
    return "hassmic" in project or "echo-dot-assist" in project


# -- crypto (hassmic's Monocypher calls, in Python) -----------------------------------------------------------

def b2k(n: int, key: bytes, msg: bytes) -> bytes:
    """Monocypher ``crypto_blake2b_keyed``: keyed BLAKE2b with an ``n``-byte digest."""
    return hashlib.blake2b(msg, key=key, digest_size=n).digest()


def x25519_public(sk: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return X25519PrivateKey.from_private_bytes(sk).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def x25519(sk: bytes, pk: bytes) -> bytes:
    """The shared secret; a low-order public key (an all-zero result) is refused, as hassmic refuses it."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

    try:
        shared = X25519PrivateKey.from_private_bytes(sk).exchange(X25519PublicKey.from_public_bytes(pk))
    except ValueError as exc:  # cryptography refuses the all-zero result itself
        raise HassmicError("the Echo's public key is a low-order point (no shared secret)") from exc
    if shared == bytes(32):
        raise HassmicError("the Echo's public key is a low-order point (no shared secret)")
    return shared


def wrap_key(sk: bytes, their: bytes, from_pk: bytes, to_pk: bytes, net_id: int) -> bytes:
    """``arb.c`` ``wrap_key``: the key that wraps K between two arbitration identities, bound to the network id."""
    msg = b"hassmic arb key\x00" + from_pk + to_pk + net_id.to_bytes(8, "little")
    return b2k(32, x25519(sk, their), msg)


def seal_network_key(hub_sk: bytes, echo_arb_pk: bytes, net_key: bytes, net_id: int,
                     nonce: bytes | None = None) -> bytes:
    """The 104-byte blob ``arb.c`` ``take_key`` opens: ``hub_pk || nonce || mac || enc(K)``."""
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

    hub_pk = x25519_public(hub_sk)
    if hub_pk == echo_arb_pk:
        raise HassmicError("HomeHub's arbitration key equals the Echo's; the Echo would refuse it")
    nonce = nonce if nonce is not None else secrets.token_bytes(24)
    key = wrap_key(hub_sk, echo_arb_pk, hub_pk, echo_arb_pk, net_id)
    out = crypto_aead_xchacha20poly1305_ietf_encrypt(net_key, hub_pk, nonce, key)
    return hub_pk + nonce + out[-16:] + out[:-16]


def open_network_key(echo_arb_sk: bytes, blob: bytes, net_id: int) -> bytes:
    """What an Echo does with a blob (``take_key``): K, or ``HassmicError`` when it does not open."""
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
    from nacl.exceptions import CryptoError

    if len(blob) != 104:
        raise HassmicError("a sealed network key is 104 bytes")
    own = x25519_public(echo_arb_sk)
    key = wrap_key(echo_arb_sk, blob[:32], blob[:32], own, net_id)
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(blob[72:] + blob[56:72], blob[:32], blob[32:56], key)
    except CryptoError as exc:
        raise HassmicError("the sealed network key does not open") from exc


def arbitration_mac_key(net_key: bytes) -> bytes:
    return b2k(32, net_key, b"hassmic arbitration mac")


def web_voucher_key(net_key: bytes) -> bytes:
    """``arb.c`` ``arb_web_key``."""
    return b2k(32, net_key, b"hassmic web voucher 1")


def voucher(voucher_key: bytes, target_pk: bytes, browser_pk: bytes, nonce: bytes, via: bytes) -> bytes:
    """``web.c`` ``voucher``: what a member of the network vouches for a browser key with."""
    return b2k(16, voucher_key, b"hassmic voucher 1" + target_pk + browser_pk + nonce + via)


def session_key(sk: bytes, peer_pk: bytes, echo_web_pk: bytes, browser_pk: bytes) -> bytes:
    """``web.c`` ``session_key``; ``sk``/``peer_pk`` are the browser's secret and the Echo's key (or the reverse)."""
    return b2k(32, x25519(sk, peer_pk), b"hassmic web 1" + echo_web_pk + browser_pk)


def request_mac(key: bytes, method: str, path: str, ctr: str, body: bytes) -> bytes:
    return b2k(16, key, f"{method}\n{path}\n{ctr}\n".encode() + body)


def response_mac(key: bytes, ctr: str, body: bytes) -> bytes:
    return b2k(16, key, f"RESP\n{ctr}\n".encode() + body)


_last_ctr = 0


def next_counter() -> str:
    """A strictly increasing request counter, microseconds since the epoch (``web/app.js`` does the same).

    One counter for every Echo is fine: each Echo only needs the counters of one browser key to grow.
    """
    global _last_ctr
    _last_ctr = max(time.time_ns() // 1000, _last_ctr + 1)
    return str(_last_ctr)


# -- beacons (arb.c) -----------------------------------------------------------------------------------------

_NODE = re.compile(r"^[a-z0-9-]{1,63}$")


@dataclass
class Beacon:
    """One ``T_BEACON`` packet: who sent it, from where, and in which network."""

    pub: bytes
    net: int
    node: str
    ip: str = ""
    ctr: int | None = None
    flags: int = 0
    raw: bytes = b""

    def verifies(self, mac_key: bytes) -> bool:
        """Whether its tag was made with this network's MAC key (the sender holds K)."""
        if not self.net or len(self.raw) < 16:
            return False
        return secrets.compare_digest(b2k(16, mac_key, self.raw[:-16]), self.raw[-16:])


def probe_beacon(pub: bytes, node: str = "homehub") -> bytes:
    """A beacon from outside any network: every Echo in one answers with its own beacon at once."""
    raw = node.encode()
    return b"HMA1\x01" + pub + bytes(8) + bytes([len(raw)]) + raw


def parse_beacon(data: bytes, ip: str = "") -> Beacon | None:
    """A beacon, or None for anything else (``arb.c`` ``on_packet``)."""
    if len(data) < 46 or data[:5] != b"HMA1\x01":
        return None
    pub, net = data[5:37], int.from_bytes(data[37:45], "little")
    nlen = data[45]
    if nlen >= 64 or len(data) < 46 + nlen:
        return None
    try:
        node = data[46:46 + nlen].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not _NODE.match(node):
        return None
    rest = data[46 + nlen:]
    if not net:
        return Beacon(pub, 0, node, ip, raw=data) if not rest else None
    if len(rest) not in (24, 25):
        return None
    ctr = int.from_bytes(rest[:8], "little")
    flags = rest[8] if len(rest) == 25 else 0
    return Beacon(pub, net, node, ip, ctr, flags, data)


class _BeaconProtocol(asyncio.DatagramProtocol):
    def __init__(self, heard: dict[bytes, Beacon], own: bytes) -> None:
        self.heard, self.own = heard, own

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        beacon = parse_beacon(data, addr[0])
        if beacon is not None and beacon.pub != self.own:
            self.heard[beacon.pub] = beacon


class BeaconScanner:
    """Sends one probe beacon and collects the answers; one scan at a time per hub, shared for a few seconds.

    HomeHub runs on the host network, so it binds the arbitration port itself (``SO_REUSEADDR``/``SO_REUSEPORT``
    beside anything else listening there) and hears the Echos' broadcast answers.
    """

    def __init__(self, *, bind_port: int = ARB_PORT, target: tuple[str, int] | None = None) -> None:
        self.bind_port = bind_port
        self.target = target or (BROADCAST_ADDR, ARB_PORT)
        self._lock = asyncio.Lock()
        self._last: tuple[float, list[Beacon]] | None = None

    def _socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self.bind_port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        return sock

    async def scan(self, own_pub: bytes, *, fresh: bool = False, timeout: float = SCAN_S) -> list[Beacon]:
        async with self._lock:
            now = time.monotonic()
            if not fresh and self._last is not None and now - self._last[0] < SCAN_CACHE_S:
                return list(self._last[1])
            heard: dict[bytes, Beacon] = {}
            try:
                sock = self._socket()
            except OSError as exc:
                raise HassmicError(f"HomeHub cannot listen for Echo beacons on UDP {self.bind_port}: {exc}. It must "
                                   "run on the host network, on the Echos' subnet.") from exc
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(lambda: _BeaconProtocol(heard, own_pub), sock=sock)
            try:
                transport.sendto(probe_beacon(own_pub), self.target)
                await asyncio.sleep(timeout)
            finally:
                transport.close()
            found = list(heard.values())
            self._last = (time.monotonic(), found)
            return found


# -- the authority: K, N and HomeHub's keys ------------------------------------------------------------------

class Authority:
    """HomeHub's Echo network: ``K``, ``N``, its arbitration key (sender of sealed keys) and its web client key.

    Made once and kept only as a sealed store blob. ``K`` is the master key of the Echo network: whoever has it
    can log in to every Echo's settings and forge arbitration claims, so it is never logged and never leaves
    HomeHub except sealed to one Echo's own key.
    """

    def __init__(self, store: Any, doc: dict[str, Any]) -> None:
        self._store = store
        self.net_key = base64.b64decode(doc["net_key"])
        self.net_id = int(doc["net_id"])
        self.hub_sk = base64.b64decode(doc["hub_sk"])
        self.web_sk = base64.b64decode(doc["web_sk"])
        if len(self.net_key) != 32 or len(self.hub_sk) != 32 or len(self.web_sk) != 32 or not 0 < self.net_id < 1 << 64:
            raise HassmicError(f"the stored Echo network ({AUTHORITY_BLOB}) is damaged")
        self.hub_pk = x25519_public(self.hub_sk)
        self.web_pk = x25519_public(self.web_sk)
        self.mac_key = arbitration_mac_key(self.net_key)
        self.voucher_key = web_voucher_key(self.net_key)

    def __repr__(self) -> str:  # never the keys
        return f"Authority(net_id={self.net_id:016x})"

    @staticmethod
    def _new_doc(net_id: int, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        doc = {"version": 1, "net_id": net_id, "net_key": base64.b64encode(secrets.token_bytes(32)).decode(),
               "hub_sk": base64.b64encode(secrets.token_bytes(32)).decode(),
               "web_sk": base64.b64encode(secrets.token_bytes(32)).decode()}
        if previous:  # a rotation keeps HomeHub's own keys: the Echos keep their approval of its web key
            doc["hub_sk"], doc["web_sk"] = previous["hub_sk"], previous["web_sk"]
        return doc

    @classmethod
    def for_hub(cls, hub: Any) -> Authority:
        """The hub's authority, made the first time (and kept sealed in the store)."""
        cached = getattr(hub, "_hassmic_authority", None)
        if cached is not None:
            return cached
        store = getattr(hub, "store", None)
        if store is None or not hasattr(store, "load_blob"):
            raise HassmicError("HomeHub has no store to keep the Echo network key in")
        try:
            doc = store.load_blob(AUTHORITY_BLOB, None)
        except Exception as exc:
            raise HassmicError(f"HomeHub cannot read its sealed Echo network key: {exc}") from exc
        if not doc:
            doc = cls._new_doc(NET_ID_START)
            cls._save(store, doc)
            log.info("hassmic: made the Echo network (id %016x); the key is sealed in the store", NET_ID_START)
        auth = cls(store, doc)
        hub._hassmic_authority = auth
        return auth

    @staticmethod
    def _save(store: Any, doc: dict[str, Any]) -> None:
        try:
            store.save_blob(AUTHORITY_BLOB, doc)
        except Exception as exc:  # no FERNET_KEY: the key must not be kept in the clear
            raise HassmicError(f"HomeHub cannot keep the Echo network key sealed: {exc}") from exc

    def rotate(self, hub: Any) -> Authority:
        """A new K one id lower (the Echos all move to it, the older network): for a lost K or an Echo given away."""
        if self.net_id <= 2:
            raise HassmicError("the Echo network id cannot go any lower")
        previous = {"hub_sk": base64.b64encode(self.hub_sk).decode(), "web_sk": base64.b64encode(self.web_sk).decode()}
        doc = self._new_doc(self.net_id - 1, previous)
        self._save(self._store, doc)
        auth = Authority(self._store, doc)
        hub._hassmic_authority = auth
        log.warning("hassmic: rotated the Echo network key (now network %016x)", auth.net_id)
        return auth


def scanner_for(hub: Any) -> BeaconScanner:
    scanner = getattr(hub, "_hassmic_scanner", None)
    if scanner is None:
        scanner = hub._hassmic_scanner = BeaconScanner()
    return scanner


# -- the settings API (web.c) ------------------------------------------------------------------------------

class EchoWeb:
    """HomeHub's signed client of one Echo's settings API."""

    def __init__(self, base_url: str, authority: Authority, *, approved_pk: str | None = None,
                 on_approved: Callable[[str], None] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = authority
        self.echo_pk: bytes | None = None
        self.hello_doc: dict[str, Any] = {}
        self._session_key: bytes | None = None
        self.approved_pk = approved_pk
        self.on_approved = on_approved

    async def _raw(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None,
                   timeout: float = WEB_TIMEOUT_S) -> tuple[int, dict[str, str], bytes]:
        import aiohttp

        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session, \
                    session.request(method, self.base_url + path, data=body if body else None,
                                    headers={"Connection": "close", **(headers or {})},
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                data = await resp.read()
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, data
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise HassmicError(f"the Echo's settings API at {self.base_url} did not answer {method} {path}: "
                               f"{exc or type(exc).__name__}") from exc

    async def hello(self) -> dict[str, Any]:
        status, _h, data = await self._raw("GET", "/api/hello")
        try:
            doc = json.loads(data)
            pub = bytes.fromhex(str(doc["pub"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise HassmicError(f"the Echo's /api/hello answer ({status}) has no web key") from exc
        if status != 200 or len(pub) != 32:
            raise HassmicError(f"the Echo's /api/hello answer ({status}) has no web key")
        if pub != self.echo_pk:
            self.echo_pk = pub
            self._session_key = session_key(self.auth.web_sk, pub, pub, self.auth.web_pk)
        self.hello_doc = doc
        return doc

    async def vouch(self) -> None:
        """``/api/vouch/nonce`` then ``/api/vouch/login`` with HomeHub's own voucher; a refusal is retried once."""
        if self.echo_pk is None:
            await self.hello()
        assert self.echo_pk is not None
        b_hex = self.auth.web_pk.hex()
        for attempt in (1, 2):
            status, _h, data = await self._raw("POST", "/api/vouch/nonce", b_hex.encode())
            try:
                nonce = bytes.fromhex(str(json.loads(data)["nonce"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise HassmicError(f"the Echo gave no login nonce ({status} {data[:80]!r})") from exc
            if len(nonce) != 16:
                raise HassmicError("the Echo's login nonce is not 16 bytes")
            mac = voucher(self.auth.voucher_key, self.echo_pk, self.auth.web_pk, nonce, VIA)
            body = f"{b_hex} {nonce.hex()} {mac.hex()} {VIA.hex()} {LABEL}".encode()
            status, _h, data = await self._raw("POST", "/api/vouch/login", body)
            try:
                answer = json.loads(data).get("login")
            except (ValueError, AttributeError):
                answer = None
            if answer == "approved":
                self.approved_pk = self.echo_pk.hex()
                if self.on_approved is not None:
                    self.on_approved(self.approved_pk)
                log.info("hassmic: %s approved HomeHub's web key", self.base_url)
                return
            if answer != "refused" or attempt == 2:
                raise HassmicError(
                    "the Echo refused HomeHub's login voucher: it does not hold HomeHub's Echo network key (it is "
                    "not in HomeHub's network yet, or its arbitration is off)" if answer == "refused"
                    else f"the Echo's login answer was not understood ({status} {data[:80]!r})")

    async def call(self, method: str, path: str, body: bytes = b"", *, timeout: float = WEB_TIMEOUT_S,
                   login: bool = True) -> tuple[int, bytes]:
        """A signed request; the answer's MAC is checked. A 401 logs in again once."""
        if self._session_key is None:
            await self.hello()
        assert self._session_key is not None
        ctr = next_counter()
        mac = request_mac(self._session_key, method, path.split("?", 1)[0], ctr, body)
        headers = {"X-HM-Pub": self.auth.web_pk.hex(), "X-HM-Ctr": ctr, "X-HM-Mac": mac.hex()}
        status, h, data = await self._raw(method, path, body, headers, timeout)
        if status == 401:
            if not login:
                raise HassmicError(f"the Echo does not accept HomeHub's login ({method} {path} answered 401)")
            await self.hello()  # its web key may be new (state wiped): the session key follows it
            await self.vouch()
            return await self.call(method, path, body, timeout=timeout, login=False)
        got = h.get("x-hm-mac")
        if got is None:
            if 200 <= status < 300:
                raise HassmicError(f"the Echo's answer to {method} {path} is not signed (firmware too old?)")
            return status, data
        if not secrets.compare_digest(got.lower(), response_mac(self._session_key, ctr, data).hex()):
            raise HassmicError(f"the Echo's answer to {method} {path} carries a wrong signature; it was ignored")
        return status, data

    async def json(self, method: str, path: str, body: bytes = b"", *, timeout: float = WEB_TIMEOUT_S) -> Any:
        status, data = await self.call(method, path, body, timeout=timeout)
        try:
            doc = json.loads(data)
        except ValueError as exc:
            raise HassmicError(f"the Echo's answer to {method} {path} ({status}) is not JSON") from exc
        if status != 200:
            err = doc.get("error") if isinstance(doc, dict) else None
            raise HassmicError(f"the Echo refused {method} {path}: {err or status}")
        return doc

    async def set(self, name: str, value: str, *, timeout: float = WEB_TIMEOUT_S) -> dict[str, Any]:
        """``POST /api/set``; a setting the Echo refused is an error with the Echo's own reason."""
        doc = await self.json("POST", "/api/set", f"{name}={value}\n".encode(), timeout=timeout)
        errors = str(doc.get("errors") or "").strip() if isinstance(doc, dict) else ""
        if errors:
            raise HassmicError(errors)
        return doc


# -- wake word models -----------------------------------------------------------------------------------------

def validate_manifest(model_id: str, raw: bytes) -> dict[str, Any]:
    """An ESPHome microWakeWord v2 manifest that hassmic takes (``mww_store.c`` ``parse_manifest``), or an error."""
    if len(raw) > MANIFEST_MAX_BYTES:
        raise HassmicError(f"wake word model {model_id}: its manifest is over {MANIFEST_MAX_BYTES} bytes")
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise HassmicError(f"wake word model {model_id}: its manifest is not JSON") from exc
    micro = doc.get("micro") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or not isinstance(micro, dict):
        raise HassmicError(f"wake word model {model_id}: its manifest has no \"micro\" section")
    if doc.get("type", "micro") != "micro":
        raise HassmicError(f"wake word model {model_id}: not a microWakeWord model (type {doc.get('type')!r})")
    if not isinstance(doc.get("wake_word"), str) or not doc["wake_word"].strip():
        raise HassmicError(f"wake word model {model_id}: its manifest names no wake word")
    cutoff = micro.get("probability_cutoff")
    if not isinstance(cutoff, int | float) or not 0 < cutoff < 1:
        raise HassmicError(f"wake word model {model_id}: its probability_cutoff is not between 0 and 1")
    if micro.get("feature_step_size", 10) != 10:
        raise HassmicError(f"wake word model {model_id}: hassmic only runs feature_step_size 10")
    window = micro.get("sliding_window_size", 5)
    if not isinstance(window, int) or not 1 <= window <= 20:
        raise HassmicError(f"wake word model {model_id}: its sliding_window_size is not 1-20")
    return doc


class WakeModelCache:
    """ESPHome's microWakeWord models, downloaded once into HomeHub's data dir and checked before use."""

    def __init__(self, directory: str, base_url: str | None = None) -> None:
        self.directory = directory
        self.base_url = (base_url or WAKE_MODELS_URL).rstrip("/")
        self._locks: dict[str, asyncio.Lock] = {}

    def _paths(self, model_id: str) -> tuple[str, str]:
        return (os.path.join(self.directory, f"{model_id}.json"), os.path.join(self.directory, f"{model_id}.tflite"))

    async def _fetch(self, url: str) -> bytes:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session, \
                    session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise HassmicError(f"{url} answered {resp.status}")
                data = await resp.content.read(MODEL_MAX_BYTES + 1)
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise HassmicError(f"{url} could not be downloaded: {exc or type(exc).__name__}") from exc
        return data

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    async def get(self, model_id: str) -> tuple[bytes, bytes]:
        """``(manifest JSON bytes, model.tflite bytes)``, from the cache or downloaded once."""
        if not MODEL_ID.match(model_id):
            raise HassmicError(f"{model_id!r} is not a wake word model id (a-z, 0-9, _ and -, at most 40)")
        lock = self._locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            jpath, mpath = self._paths(model_id)
            if os.path.isfile(jpath) and os.path.isfile(mpath):
                manifest = await asyncio.to_thread(_read, jpath)
                model = await asyncio.to_thread(_read, mpath)
                validate_manifest(model_id, manifest)
                if 0 < len(model) <= MODEL_MAX_BYTES:
                    return manifest, model
            manifest = await self._fetch(f"{self.base_url}/{model_id}.json")
            validate_manifest(model_id, manifest)
            model = await self._fetch(f"{self.base_url}/{model_id}.tflite")
            if not 0 < len(model) <= MODEL_MAX_BYTES:
                raise HassmicError(f"wake word model {model_id}: model.tflite is empty or over 1 MiB")
            os.makedirs(self.directory, exist_ok=True)
            await asyncio.to_thread(self._write, mpath, model)
            await asyncio.to_thread(self._write, jpath, manifest)
            log.info("hassmic: downloaded wake word model %s (%d bytes)", model_id, len(model))
            return manifest, model


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def wake_models_option(options: dict[str, Any]) -> list[str]:
    """The entry option ``echo_wake_models`` (a list, or a comma separated string), checked."""
    raw = options.get("echo_wake_models")
    if raw is None:
        return list(DEFAULT_WAKE_MODELS)
    items = [s.strip() for s in raw.split(",")] if isinstance(raw, str) else list(raw) if isinstance(raw, list | tuple) else None
    if items is None:
        raise HassmicError("the echo_wake_models option must be a list of model ids")
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if not isinstance(item, str) or not MODEL_ID.match(item):
            raise HassmicError(f"echo_wake_models: {item!r} is not a model id")
        if item not in out:
            out.append(item)
    if len(out) > 16:
        raise HassmicError("echo_wake_models: an Echo holds at most 16 models")
    return out


# -- one Echo's management ----------------------------------------------------------------------------------

Notifier = Callable[[dict[str, Any]], Awaitable[Any]]


async def _send_notice(payload: dict[str, Any]) -> Any:
    from services.common import notify

    return await notify.send(payload)


@dataclass
class _Mic:
    """What the log said last time, and the current dead-microphone episode."""

    tail: str | None = None
    dead: bool = False
    since: float | None = None
    rebooted_at: float | None = None
    notified: bool = False


class HassmicManager:
    """Everything HomeHub does for one hassmic Echo, beside the ESPHome driver that holds its keyed connection."""

    #: unique_id suffix -> (domain, name, entity category, device class)
    ENTITIES: ClassVar[dict[str, tuple[str, str, str, str | None]]] = {
        "hassmic_wake_engine": ("select", "Wake word engine", "config", None),
        "hassmic_online_updates": ("select", "Online updates", "config", None),
        "hassmic_wake_models": ("sensor", "Wake word models", "diagnostic", None),
        "hassmic_microphone": ("binary_sensor", "Microphone", "diagnostic", "problem"),
        "hassmic_management": ("sensor", "Management", "diagnostic", None),
    }

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self.hub = driver.hub
        self.client: Any = None
        self.device_info: Any = None
        self.arb_service: Any = None
        self.web: EchoWeb | None = None
        self.web_url: str | None = None
        self.in_network = False
        self.logged_in = False
        self.reason: str | None = None
        self.engine: str | None = None
        self.online_updates: str | None = None
        self.mww: dict[str, Any] = {}
        self.models_reason: str | None = None
        self.mic = _Mic()
        self.notifier: Notifier = _send_notice
        self.scanner: BeaconScanner | None = None  # tests: a fake; otherwise the hub's
        self.model_cache: WakeModelCache | None = None
        self.mic_check_s = 60.0
        self.clock: Callable[[], float] = time.time
        self.reconcile_s = 300.0
        self._tasks: set[asyncio.Task] = set()
        self._runner: asyncio.Task | None = None
        self._busy = asyncio.Lock()
        self._channel_nudged = False
        self.log = logging.getLogger(f"homehub.hassmic.{driver.device_name or driver.host}")
        self._restore()

    # -- persisted per-Echo state (never secret: the approved Echo key and the reboot times) ------------------
    @property
    def _kv_key(self) -> str:
        return f"hassmic:{self.driver.entry.entry_id}"

    def _restore(self) -> None:
        store = getattr(self.hub, "store", None)
        doc = {}
        if store is not None and hasattr(store, "get_kv"):
            with contextlib.suppress(Exception):
                doc = store.get_kv(self._kv_key) or {}
        self._approved_pk: str | None = doc.get("approved_pk")
        self.last_reboot: float = float(doc.get("last_reboot") or 0)
        ep = doc.get("mic_episode") or {}
        self.mic.since, self.mic.rebooted_at = ep.get("since"), ep.get("rebooted_at")
        self.mic.notified = bool(ep.get("notified"))

    def _save(self) -> None:
        store = getattr(self.hub, "store", None)
        if store is None or not hasattr(store, "set_kv"):
            return
        ep = ({"since": self.mic.since, "rebooted_at": self.mic.rebooted_at, "notified": self.mic.notified}
              if self.mic.since is not None else None)
        try:
            store.set_kv(self._kv_key, {"approved_pk": self._approved_pk, "last_reboot": self.last_reboot,
                                        "mic_episode": ep})
        except Exception as exc:
            self.log.warning("hassmic %s: its state could not be saved: %s", self.name, exc)

    def _approved(self, pk: str) -> None:
        self._approved_pk = pk
        self._save()

    # -- what it is -------------------------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.driver.device_name or self.driver.host)

    @property
    def title(self) -> str:
        return str(getattr(self.device_info, "friendly_name", "") or self.driver.entry.title or self.name)

    def _entity(self, key: str) -> EntityEntry | None:
        return next((e for e in self.driver.entities if (e.unique_id or "").endswith(f"-{key}")
                     and "/" not in (e.unique_id or "")), None)

    def owns(self, entity: EntityEntry) -> bool:
        uid = entity.unique_id or ""
        return "/" not in uid and uid.rsplit("-", 1)[-1] in self.ENTITIES

    def ensure_entities(self, device_info: Any) -> list[EntityEntry]:
        """Register the Echo's management entities (HA-style ``{MAC}-{key}`` unique ids) the registry lacks."""
        from services.homehub.discovery import norm_mac

        mac = norm_mac(str(getattr(device_info, "mac_address", "") or ""))
        if mac is None:
            return []
        mac_upper = ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper()
        connections = [["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]]
        title = getattr(device_info, "friendly_name", "") or getattr(device_info, "name", "") or self.driver.entry.title
        device = None
        added = []
        local = getattr(self.driver, "local_entities", None)
        for key, (domain, name, category, device_class) in self.ENTITIES.items():
            uid = f"{mac_upper}-{key}"
            ent = self.driver.registry_entity(domain, uid)
            if ent is None:
                if device is None:
                    device = self.driver.ensure_device(title, connections=connections)
                info: dict[str, Any] = {"entity_category": category}
                if device_class:
                    info["original_device_class"] = device_class
                ent = self.driver.ensure_entity(domain, uid, name, device=device, **info)
                added.append(ent)
            if isinstance(local, list) and all(e.entity_id != ent.entity_id for e in local):
                local.append(ent)
        if added:
            self.log.info("hassmic %s: registered %s", self.name, ", ".join(e.entity_id for e in added))
        return added

    # -- connection -------------------------------------------------------------------------------------------
    def attach(self, client: Any, device_info: Any, services: list[Any]) -> None:
        """A new keyed connection: register the entities and start the bring-up in the background."""
        self.client, self.device_info = client, device_info
        self.arb_service = next((s for s in services or [] if getattr(s, "name", "") == "arbitration_key"), None)
        self.in_network = False
        try:
            self.ensure_entities(device_info)
        except Exception:
            self.log.exception("hassmic %s: registering its entities failed", self.name)
        self._publish_management("starting")
        self._stop_runner()
        self._runner = asyncio.get_running_loop().create_task(self._run(client), name=f"hassmic:{self.name}")

    def detach(self) -> None:
        """The connection is gone: stop the background work, show the entities unavailable."""
        self._stop_runner()
        self.client = None
        self.in_network = False
        for key in self.ENTITIES:
            ent = self._entity(key)
            if ent is not None:
                self.hub.publish(ent, UNAVAILABLE, {"reason": "the Echo is not connected to HomeHub"})

    def close(self) -> None:
        self._stop_runner()
        for t in list(self._tasks):
            t.cancel()

    def _stop_runner(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None and not runner.done():
            runner.cancel()

    def _spawn(self, coro: Any, what: str) -> None:
        task = asyncio.get_running_loop().create_task(coro, name=f"hassmic:{self.name}:{what}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, client: Any) -> None:
        """Bring the Echo up, then check its microphone every minute and reconcile every few minutes."""
        loop = asyncio.get_running_loop()
        while self.client is client:
            try:
                await self.bring_up()
            except asyncio.CancelledError:
                raise
            except HassmicError as exc:
                self._fail(str(exc))
            except Exception as exc:
                self.log.exception("hassmic %s: bring-up failed", self.name)
                self._fail(f"HomeHub hit an unexpected error: {exc}")
            until = loop.time() + self.reconcile_s
            while self.client is client and loop.time() < until:
                if self.logged_in:
                    try:
                        await self.check_microphone()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._publish_mic(UNKNOWN, f"its log could not be read: {exc}")
                await asyncio.sleep(self.mic_check_s)

    async def ensure_encryption(self) -> bool:
        """Give an Echo adopted unencrypted its own Noise key; True when it now reconnects with one.

        hassmic takes its first key only over a zero-PSK connection, which adoption did not use before,
        so Echos were added plaintext: no key means no clock from the hub, no Sendspin pairing, no
        update installs and no Echo network key (each needs the "keyed" connection). The key is saved
        on the entry and the connection dropped; the driver reconnects with it.
        """
        drv = self.driver
        if getattr(drv, "noise_psk", None):
            return False
        from services.homehub import flows

        try:
            psk = await flows.esphome_provision_noise_psk(str(drv.host), int(drv.port), str(drv.password or ""))
        except Exception as exc:
            raise HassmicError(f"it is not encrypted and did not take an encryption key: {exc or type(exc).__name__}"
                               ) from exc
        if not psk:
            raise HassmicError("it is not encrypted and refused an encryption key; restart it and HomeHub tries again")
        entry = drv.entry
        entry.data = {**dict(entry.data or {}), "noise_psk": psk}
        store = getattr(self.hub, "store", None)
        if store is not None:
            store.save_config_entry(entry)
        drv.noise_psk = psk
        self.log.warning("hassmic %s: it was added unencrypted; gave it an encryption key and reconnecting with it",
                         self.name)
        client = self.client
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
        return True

    async def bring_up(self) -> None:
        """Encryption, network key, web login, entity states (in that order; each needs the one before)."""
        if await self.ensure_encryption():
            return  # the driver reconnects with the key; attach() runs the bring-up again
        async with self._busy:
            network_error: HassmicError | None = None
            try:
                await self.ensure_network()
            except HassmicError as exc:
                # An Echo that approved HomeHub before keeps that approval (state/web_clients) even with its
                # arbitration off, so its settings stay reachable: the engine select can turn it back on.
                if not (self._approved_pk and (self.web_url or self._node_web_url())):
                    raise
                network_error = exc
            await self.ensure_login()
            await self.refresh()
            if self.engine == "microwakeword":
                await self._ensure_models_shown()
            if network_error is not None:
                self.reason = str(network_error)
                self.log.warning("hassmic %s: %s (its settings are still reachable)", self.name, self.reason)
                self._publish_management("error")
                return
            self.reason = None
            self._publish_management("ready")
        if not self._channel_nudged:
            self._channel_nudged = True
            self._spawn(self._nudge_online_updates(), "online updates")

    async def _nudge_online_updates(self) -> None:
        """Let echo_ota put a newly adopted Echo on its update channel now, rather than in up to five minutes."""
        from services.homehub import echo_ota

        try:
            await echo_ota.ensure_channel(self.hub)
        except Exception as exc:
            self.log.warning("hassmic %s: setting its update channel failed: %s", self.name, exc)

    def _fail(self, reason: str) -> None:
        self.reason = reason
        self.log.warning("hassmic %s: %s", self.name, reason)
        self._publish_management("error")
        attrs = {"reason": reason}
        for key in ("hassmic_wake_engine", "hassmic_online_updates", "hassmic_wake_models"):
            ent = self._entity(key)
            if ent is not None:
                self.hub.publish(ent, UNAVAILABLE, attrs)
        if not self.logged_in:
            self._publish_mic(UNAVAILABLE, reason)

    # -- step 1: the network key --------------------------------------------------------------------------------
    async def _find_beacon(self, auth: Authority, *, fresh: bool = False) -> Beacon:
        node = str(getattr(self.device_info, "name", "") or self.driver.device_name)
        scanner = self.scanner or scanner_for(self.hub)
        beacons = [b for b in await scanner.scan(auth.hub_pk, fresh=fresh) if b.node == node]
        if not beacons:
            raise HassmicError(f"no arbitration beacon from {node} on UDP {ARB_PORT}. HomeHub must run on the host "
                               "network on the Echo's subnet, and the Echo must be running arbitration.")
        host = str(self.driver.host)
        return next((b for b in beacons if b.ip == host), beacons[-1])

    async def ensure_network(self) -> None:
        """Make sure the Echo holds HomeHub's network key K (in network N); hand it over if not."""
        if self.arb_service is None:  # checked first: nothing is made or sent for an Echo that cannot take it
            self.in_network = False
            raise HassmicError(ARBITRATION_OFF)
        auth = Authority.for_hub(self.hub)
        beacon = await self._find_beacon(auth)
        self.web_url = f"http://{beacon.ip}:{WEB_PORT}" if beacon.ip else None
        if beacon.net == auth.net_id and beacon.verifies(auth.mac_key):
            self.in_network = True
            return
        if beacon.net == auth.net_id:
            raise HassmicError(f"the Echo is in network {auth.net_id:016x} but not with HomeHub's key (its beacon tag "
                               "does not verify): another controller used the same network id")
        if beacon.net and beacon.net < auth.net_id:
            raise HassmicError(f"the Echo is in an older network ({beacon.net:016x}) than HomeHub's ({auth.net_id:016x}), "
                               "so it refuses HomeHub's key")
        blob = seal_network_key(auth.hub_sk, beacon.pub, auth.net_key, auth.net_id)
        client = self.client
        if client is None:
            raise DeviceUnavailable(f"ESPHome {self.name} is not connected")
        try:
            await asyncio.wait_for(client.execute_service(self.arb_service, {
                "network": f"{auth.net_id:016x}", "key": base64.b64encode(blob).decode()}), 5)
        except Exception as exc:
            raise HassmicError(f"handing the Echo network key to the Echo failed: {exc or type(exc).__name__}") from exc
        self.log.info("hassmic %s: handed it the Echo network key (network %016x)", self.name, auth.net_id)
        loop = asyncio.get_running_loop()
        until = loop.time() + CONFIRM_S
        while loop.time() < until:
            await asyncio.sleep(CONFIRM_POLL_S)
            beacon = await self._find_beacon(auth, fresh=True)
            if beacon.net == auth.net_id and beacon.verifies(auth.mac_key):
                self.in_network = True
                self.log.info("hassmic %s: joined HomeHub's Echo network", self.name)
                return
        raise HassmicError("the Echo did not join HomeHub's Echo network after it was handed the key (it beacons "
                           f"network {beacon.net:016x}); its log says why")

    # -- step 2: the web login ------------------------------------------------------------------------------------
    def _node_web_url(self) -> str | None:
        """The node's own "Web UI address" text sensor (``http://<address>:28931``), if it sent one."""
        from aioesphomeapi import TextSensorInfo

        for k, info in (getattr(self.driver, "_infos", {}) or {}).items():
            if isinstance(info, TextSensorInfo) and getattr(info, "object_id", "") == "web_ui_address":
                state = getattr((getattr(self.driver, "_last", {}) or {}).get(k), "state", "")
                if isinstance(state, str) and re.match(r"^http://[0-9.]+:\d+$", state):
                    return state
        return None

    async def ensure_login(self) -> None:
        auth = Authority.for_hub(self.hub)
        self.web_url = self.web_url or self._node_web_url()
        if not self.web_url:
            raise HassmicError("HomeHub does not know the Echo's address for its settings API")
        if self.web is None or self.web.base_url != self.web_url or self.web.auth is not auth:
            self.web = EchoWeb(self.web_url, auth, approved_pk=self._approved_pk, on_approved=self._approved)
        hello = await self.web.hello()
        if self._approved_pk != str(hello.get("pub", "")).lower():
            await self.web.vouch()
        self.logged_in = True

    def _require_web(self) -> EchoWeb:
        if self.web is None or not self.logged_in:
            raise HassmicError(self.reason or "HomeHub is not logged in to this Echo's settings yet")
        return self.web

    # -- step 3: what the Echo says -------------------------------------------------------------------------------
    async def refresh(self) -> None:
        web = self._require_web()
        state = await web.json("GET", "/api/state")
        settings = {s.get("name"): s for s in state.get("settings") or [] if isinstance(s, dict)}
        self.engine = _choice(settings.get("wake_engine"))
        self.online_updates = _choice(settings.get("online_updates"))
        self.mww = await web.json("GET", "/api/mww")
        self._publish_settings()
        self._publish_models()

    def _publish_settings(self) -> None:
        ent = self._entity("hassmic_wake_engine")
        if ent is not None:
            opts = {"options": list(ENGINE_OPTIONS.values())}
            if self.engine in ENGINE_OPTIONS:
                self.hub.publish(ent, ENGINE_OPTIONS[self.engine], opts)
            elif self.engine == "homeassistant":
                self.hub.publish(ent, UNKNOWN, {**opts, "reason": HA_ENGINE})
            else:
                self.hub.publish(ent, UNKNOWN, {**opts, "reason": "the Echo did not report its wake word engine"})
        ent = self._entity("hassmic_online_updates")
        if ent is not None:
            opts = {"options": list(UPDATE_OPTIONS.values())}
            if self.online_updates in UPDATE_OPTIONS:
                self.hub.publish(ent, UPDATE_OPTIONS[self.online_updates], opts)
            else:
                self.hub.publish(ent, UNKNOWN, {**opts, "reason": "the Echo did not report its online updates setting"})

    def installed_models(self) -> list[str]:
        return sorted(str(m.get("id")) for m in self.mww.get("models") or [] if isinstance(m, dict) and m.get("id"))

    def _publish_models(self) -> None:
        ent = self._entity("hassmic_wake_models")
        if ent is None:
            return
        installed = self.installed_models()
        try:
            wanted = wake_models_option(self.driver.entry.options)
        except HassmicError as exc:
            wanted, self.models_reason = [], str(exc)
        attrs: dict[str, Any] = {
            "models": installed, "wanted": wanted, "missing": [m for m in wanted if m not in installed],
            "names": {str(m.get("id")): m.get("name") for m in self.mww.get("models") or [] if isinstance(m, dict)},
            "active": self.mww.get("active"), "second": self.mww.get("second")}
        if self.models_reason:
            attrs["reason"] = self.models_reason
        self.hub.publish(ent, (", ".join(installed) or "none")[:255], attrs)

    def _publish_management(self, state: str) -> None:
        ent = self._entity("hassmic_management")
        if ent is None:
            return
        auth = getattr(self.hub, "_hassmic_authority", None)
        attrs: dict[str, Any] = {"in_network": self.in_network, "logged_in": self.logged_in,
                                 "network": f"{auth.net_id:016x}" if auth is not None else None,
                                 "web_url": self.web_url}
        if self.reason and state == "error":
            attrs["reason"] = self.reason
        self.hub.publish(ent, state, attrs)

    # -- device page actions --------------------------------------------------------------------------------------
    async def call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> None:
        key = (entity.unique_id or "").rsplit("-", 1)[-1]
        if domain != "select" or service != "select_option":
            raise ServiceNotFound(f"{domain}.{service} is not offered by {entity.entity_id}")
        option = str(data.get("option") or "")
        if key == "hassmic_wake_engine":
            await self.set_engine(option)
        elif key == "hassmic_online_updates":
            await self.set_online_updates(option)
        else:
            raise ServiceNotFound(f"{entity.entity_id} has nothing to select")

    async def set_engine(self, option: str) -> None:
        engine = next((k for k, v in ENGINE_OPTIONS.items() if option.lower() in (k, v.lower())), None)
        if engine is None:
            raise HubError(f"{option!r} is not a wake word engine: {', '.join(ENGINE_OPTIONS.values())}")
        web = self._require_web()
        async with self._busy:
            if engine == "microwakeword":
                await self.ensure_models()
            await web.set("wake_engine", engine)
            self.log.info("hassmic %s: wake word engine set to %s", self.name, ENGINE_OPTIONS[engine])
            await self.refresh()
        voice = getattr(self.driver, "voice", None)
        if voice is not None and getattr(voice, "client", None) is not None:
            await voice.load_wake_words()  # the Echo offers other wake words with the other engine

    async def set_online_updates(self, option: str) -> None:
        value = next((k for k, v in UPDATE_OPTIONS.items() if option.lower() in (k, v.lower())), None)
        if value is None:
            raise HubError(f"{option!r} is not an online updates choice: {', '.join(UPDATE_OPTIONS.values())}")
        web = self._require_web()
        async with self._busy:
            try:
                await web.set("online_updates", value, timeout=SET_TIMEOUT_S)
            except HassmicError as exc:
                ent = self._entity("hassmic_online_updates")
                if ent is not None:
                    cur = self.hub.states.get(ent.entity_id)
                    self.hub.publish(ent, cur.state if cur else UNKNOWN,
                                     {"options": list(UPDATE_OPTIONS.values()), "reason": str(exc)})
                raise
            self.log.info("hassmic %s: online updates set to %s", self.name, UPDATE_OPTIONS[value])
            await self.refresh()

    # -- wake word models -----------------------------------------------------------------------------------------
    def _cache(self) -> WakeModelCache:
        if self.model_cache is None:
            data_dir = getattr(self.hub, "data_dir", None)
            if not data_dir:
                raise HassmicError("HomeHub has no data directory to keep wake word models in")
            self.model_cache = getattr(self.hub, "_hassmic_models", None) or WakeModelCache(
                os.path.join(data_dir, "hassmic_wake_models"))
            self.hub._hassmic_models = self.model_cache
        return self.model_cache

    async def ensure_models(self) -> list[str]:
        """Install the ``echo_wake_models`` the Echo lacks (``POST /api/mww/add/<id>``); the ids it added."""
        web = self._require_web()
        try:
            wanted = wake_models_option(self.driver.entry.options)
            installed = set(self.installed_models())
            added = []
            for model_id in wanted:
                if model_id in installed:
                    continue
                manifest, model = await self._cache().get(model_id)
                body = str(len(manifest)).encode() + b"\n" + manifest + model
                doc = await web.json("POST", f"/api/mww/add/{model_id}", body, timeout=UPLOAD_TIMEOUT_S)
                if not (isinstance(doc, dict) and doc.get("ok")):
                    raise HassmicError(f"the Echo did not take wake word model {model_id}")
                added.append(model_id)
                self.log.info("hassmic %s: installed wake word model %s", self.name, model_id)
            self.mww = await web.json("GET", "/api/mww")
        except HassmicError as exc:
            self.models_reason = str(exc)
            with contextlib.suppress(HassmicError):  # what did get installed before the failure
                self.mww = await web.json("GET", "/api/mww")
            self._publish_models()
            raise
        self.models_reason = None
        self._publish_models()
        return added

    async def _ensure_models_shown(self) -> None:
        with contextlib.suppress(HassmicError):  # the reason is on the models sensor; the Echo still works
            await self.ensure_models()

    # -- the microphone -------------------------------------------------------------------------------------------
    def _publish_mic(self, state: str, reason: str | None = None) -> None:
        ent = self._entity("hassmic_microphone")
        if ent is None:
            return
        attrs: dict[str, Any] = {}
        if reason:
            attrs["reason"] = reason
        if self.last_reboot:
            attrs["last_automatic_reboot"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.last_reboot))
        self.hub.publish(ent, state, attrs)

    @staticmethod
    def mic_dead(lines: list[str]) -> int:
        """How many of the newest lines say the processed mic stream is dead (0 when the newest are not that)."""
        recent = lines[-MIC_RECENT_LINES:]
        hits = [i for i, line in enumerate(recent) if MIC_DEAD_LINE in line]
        if len(hits) < MIC_DEAD_LINES or hits[-1] < len(recent) - MIC_DEAD_TAIL:
            return 0
        return len(hits)

    async def check_microphone(self) -> bool:
        """Read the Echo's log; report a dead mic stream and remedy it. True when it is dead."""
        web = self._require_web()
        status, data = await web.call("GET", "/api/log/0", timeout=20)
        if status != 200:
            raise HassmicError(f"the Echo's log answered {status}")
        text = data.decode("utf-8", "replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        # Only what was logged since the last look counts (hassmic stamps every line, so the last line seen is
        # found again). A dead stream logs its line every few seconds; a log that did not grow, or grew with
        # other lines, is an Echo that got over it.
        tail = lines[-1] if lines else ""
        new = lines[-MIC_RECENT_LINES:]
        if self.mic.tail is not None:
            seen = next((i for i in range(len(lines) - 1, -1, -1) if lines[i] == self.mic.tail), None)
            if seen is not None:  # not found: the log rotated, so its newest lines are all new
                new = lines[seen + 1:]
        hits = self.mic_dead(new)
        self.mic.tail = tail
        if not hits:
            if self.mic.since is not None:
                self.log.info("hassmic %s: its microphone works again", self.name)
            self.mic = _Mic(tail=tail)
            self._save()
            self._publish_mic("off")
            return False
        self.mic.dead = True
        now = self.clock()
        if self.mic.since is None:
            self.mic.since = now
            self.log.warning("hassmic %s: its microphone delivers nothing (%d of its last log lines say so)",
                             self.name, hits)
        reason = ("the Echo's processed microphone stream delivers nothing (its log repeats \"micAsr delivers "
                  "nothing\"), so it hears no wake word")
        await self._remedy(now)
        if self.mic.rebooted_at and self.mic.rebooted_at >= now - 1:
            reason += "; HomeHub rebooted it"
        elif self.mic.notified:
            reason += "; a reboot did not fix it and the admins were told"
        self._publish_mic("on", reason)
        return True

    async def _remedy(self, now: float) -> None:
        if now - self.last_reboot >= REBOOT_EVERY_S:
            restarts = getattr(self.driver, "restarts", None)
            if restarts is not None:  # the restart that follows is the hub's doing, in the device's Restarts
                with contextlib.suppress(Exception):
                    restarts.note("microphone_reboot", True)
            how = await self.reboot()
            self.last_reboot = self.mic.rebooted_at = now
            self._save()
            self.log.warning("hassmic %s: rebooted it (%s) for its dead microphone", self.name, how)
            return
        helped_not = self.mic.rebooted_at is not None and now - self.mic.rebooted_at >= REBOOT_SETTLE_S
        blocked = self.mic.rebooted_at is None  # rebooted for an earlier episode within the hour
        if (helped_not or blocked) and not self.mic.notified:
            self.mic.notified = True
            self._save()
            await self._notify(rebooted=not blocked)

    async def reboot(self) -> str:
        """A full reboot (what a power cycle does): the settings API's, else the node's "Restart" button."""
        web = self.web
        if web is not None and self.logged_in:
            try:
                doc = await web.json("POST", "/api/reboot", b"reboot")
                if isinstance(doc, dict) and doc.get("reboot"):
                    return "settings API"
            except HassmicError as exc:
                self.log.warning("hassmic %s: the settings API did not reboot it: %s", self.name, exc)
        from aioesphomeapi import ButtonInfo

        infos = getattr(self.driver, "_infos", {}) or {}
        button = next((i for i in infos.values() if isinstance(i, ButtonInfo) and getattr(i, "object_id", "") == "restart"), None)
        if button is None or self.client is None:
            raise HassmicError("the Echo could not be rebooted: no settings API login and no Restart button")
        self.client.button_command(button.key, device_id=getattr(button, "device_id", 0) or 0)
        return "Restart button"

    async def _notify(self, *, rebooted: bool) -> None:
        from services.common import notify

        entry_id = self.driver.entry.entry_id
        body = (f"{self.title}'s microphone delivers nothing, so it does not hear its wake word. "
                + ("HomeHub rebooted it and that did not help. " if rebooted
                   else "HomeHub already rebooted it within the last hour. ")
                + "Unplug it for ten seconds; if it stays deaf, see Devices, " + self.title + ".")
        payload = notify.notice(
            "@admins", "admin", f"{self.title} cannot hear", body,
            link=notify.link("devices", hash="setup", focus=f"entry-{entry_id}"), priority=3,
            tag=f"admin:hassmic-mic:{entry_id}", once=f"homehub:hassmic-mic:{entry_id}:{int(self.mic.since or 0)}",
            kind="admin", audience="parents")
        try:
            await self.notifier(payload)
        except Exception as exc:
            self.log.warning("hassmic %s: the microphone notice was not sent: %s", self.name, exc)

    def status(self) -> dict[str, Any]:
        return {"in_network": self.in_network, "logged_in": self.logged_in, "reason": self.reason,
                "engine": self.engine, "online_updates": self.online_updates, "models": self.installed_models(),
                "microphone_dead": self.mic.dead, "web_url": self.web_url}


def _choice(setting: dict[str, Any] | None) -> str | None:
    """A ``/api/state`` choice setting's value: its ``value`` is the index into ``choices``."""
    if not setting:
        return None
    value, choices = setting.get("value"), setting.get("choices") or []
    if isinstance(value, int) and 0 <= value < len(choices):
        return str(choices[value]).lower()
    if isinstance(value, str):
        return value.lower()
    return None
