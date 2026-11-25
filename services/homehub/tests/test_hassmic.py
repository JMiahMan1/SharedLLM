"""hassmic Echo management: the network key, the vouch login, signed calls and the device-page entities.

``FakeEcho`` is a settings API and an arbitration identity on 127.0.0.1 written from hassmic's C (``arb.c``
``take_key``, ``web.c`` vouch/signed_ok/respond_s, ``mww_store.c``) with hashlib, cryptography and PyNaCl directly,
not with ``hassmic.py``; its math is the math ``test_hassmic_crypto.py`` checks against hassmic's own C vectors.
Every value here is made up (MACs 02:00:00:00:00:xx, addresses 192.168.10.x).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
from typing import Any, ClassVar

import pytest
from aioesphomeapi import (
    ButtonInfo,
    DeviceInfo,
    TextSensorInfo,
    TextSensorState,
    UserService,
    UserServiceArg,
    UserServiceArgType,
)
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt

from services.homehub import echo_ota, hassmic
from services.homehub.core import HubError
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub

MAC = "02:00:00:00:00:21"
NODE = "echo-kitchen"
DATA = {"host": "127.0.0.1", "port": 6053, "password": "", "noise_psk": "not-a-real-key", "device_name": NODE}
DEAD = "audio: micAsr delivers nothing (status 110), reopening"
REOPEN = "audio: micAsr rate=16000 ch=1 bits=16"
_clock = [100.0]


def stamped(*lines: str) -> list[str]:
    """Lines as hassmic writes them to boot.log (clock.c stamps each, here with seconds since boot)."""
    out = []
    for line in lines:
        _clock[0] += 1.5
        out.append(f"boot+{_clock[0]:.3f} {line}")
    return out


def _pub(sk: bytes) -> bytes:
    return X25519PrivateKey.from_private_bytes(sk).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _x(sk: bytes, pk: bytes) -> bytes:
    return X25519PrivateKey.from_private_bytes(sk).exchange(X25519PublicKey.from_public_bytes(pk))


def _b2k(n: int, key: bytes, msg: bytes) -> bytes:
    return hashlib.blake2b(msg, key=key, digest_size=n).digest()


class FakeEcho:
    """One hassmic Echo: its arbitration identity and network, and its settings API."""

    def __init__(self, node: str = NODE) -> None:
        self.node = node
        self.arb_sk, self.web_sk = os.urandom(32), os.urandom(32)
        self.arb_pk, self.web_pk = _pub(self.arb_sk), _pub(self.web_sk)
        self.net_id = int.from_bytes(os.urandom(8), "little") | (1 << 63)  # a random network of its own
        self.net_key = os.urandom(32)
        self.arbitrating = True
        self.keys_taken = 0
        self.clients: dict[bytes, dict[str, Any]] = {}
        self.nonces: dict[bytes, bytes] = {}
        self.settings = {"wake_engine": "amazon", "online_updates": "off"}
        self.models: dict[str, dict[str, Any]] = {}
        self.manifests_added: list[bytes] = []
        self.edits: list[tuple[str, str]] = []
        self.log_lines: list[str] = ["hassmic: started", "voice: listening"]
        self.reboots = 0
        self.github = True
        self.forge = False
        self.calls: list[tuple[str, str]] = []
        self.port = 0
        self._runner: web.AppRunner | None = None

    # -- arbitration --------------------------------------------------------------------------------------
    def take_key(self, network: str, key: str) -> None:
        """arb.c take_key: a lower id than ours, a blob that opens with our identity and that id."""
        blob = base64.b64decode(key)
        nid = int(network, 16)
        if not self.arbitrating or len(blob) != 104 or not nid or nid >= self.net_id or blob[:32] == self.arb_pk:
            return
        msg = b"hassmic arb key\x00" + blob[:32] + self.arb_pk + nid.to_bytes(8, "little")
        wrap = _b2k(32, _x(self.arb_sk, blob[:32]), msg)
        try:
            k = crypto_aead_xchacha20poly1305_ietf_decrypt(blob[72:] + blob[56:72], blob[:32], blob[32:56], wrap)
        except Exception:
            return
        self.net_key, self.net_id = k, nid
        self.keys_taken += 1

    def beacon(self) -> bytes:
        name = self.node.encode()
        raw = b"HMA1\x01" + self.arb_pk + self.net_id.to_bytes(8, "little") + bytes([len(name)]) + name
        raw += (12345).to_bytes(8, "little")
        return raw + _b2k(16, _b2k(32, self.net_key, b"hassmic arbitration mac"), raw)

    # -- web ----------------------------------------------------------------------------------------------
    def _session(self, pub: bytes) -> bytes:
        return _b2k(32, _x(self.web_sk, pub), b"hassmic web 1" + self.web_pk + pub)

    def _signed(self, req: web.Request, body: bytes) -> bytes | None:
        try:
            pub = bytes.fromhex(req.headers["X-HM-Pub"])
            ctr_s = req.headers["X-HM-Ctr"]
            ctr, mac = int(ctr_s), bytes.fromhex(req.headers["X-HM-Mac"])
        except (KeyError, ValueError):
            return None
        client = self.clients.get(pub)
        if client is None or ctr <= client["floor"] or ctr in client["seen"]:
            return None
        want = _b2k(16, self._session(pub), f"{req.method}\n{req.path}\n{ctr_s}\n".encode() + body)
        if want != mac:
            return None
        client["seen"].add(ctr)
        return pub

    def _answer(self, req: web.Request, pub: bytes, status: int, payload: Any, text: bool = False) -> web.Response:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        mac = _b2k(16, self._session(pub), f"RESP\n{req.headers['X-HM-Ctr']}\n".encode() + body)
        if self.forge:
            mac = bytes(16)
        return web.Response(status=status, body=body, headers={"X-HM-Mac": mac.hex()},
                            content_type="text/plain" if text else "application/json")

    async def handle(self, req: web.Request) -> web.Response:
        body = await req.read()
        path = req.path
        self.calls.append((req.method, path))
        if path == "/api/hello":
            return web.json_response({"name": "Kitchen Echo", "node": self.node, "pub": self.web_pk.hex(), "members": []})
        if path == "/api/vouch/nonce":
            try:
                pub = bytes.fromhex(body.decode())
                assert len(pub) == 32
            except (ValueError, AssertionError):
                return web.json_response({"error": "key"}, status=400)
            nonce = os.urandom(16)
            self.nonces[nonce] = pub
            return web.json_response({"nonce": nonce.hex()})
        if path == "/api/vouch/login":
            parts = body.decode().split(" ", 4)
            pub, nonce, mac, via = (bytes.fromhex(p) for p in parts[:4])
            if self.nonces.pop(nonce, None) != pub or not self.arbitrating:
                return web.json_response({"login": "refused"})
            wk = _b2k(32, self.net_key, b"hassmic web voucher 1")
            if _b2k(16, wk, b"hassmic voucher 1" + self.web_pk + pub + nonce + via) != mac:
                return web.json_response({"login": "refused"})
            client = self.clients.setdefault(pub, {"floor": 0, "seen": set()})
            client["label"] = f"{parts[4][:30]} via {via.decode()[:28]}"
            return web.json_response({"login": "approved"})
        pub = self._signed(req, body)
        if pub is None:
            return web.json_response({"error": "not logged in"}, status=401)
        return self._route(req, pub, body)

    def _route(self, req: web.Request, pub: bytes, body: bytes) -> web.Response:
        path = req.path
        if path == "/api/state":
            choices = {"wake_engine": ["amazon", "microwakeword", "homeassistant"],
                       "online_updates": ["off", "beta", "release"]}
            settings = [{"name": n, "type": "choice", "choices": c, "value": c.index(self.settings[n])}
                        for n, c in choices.items()]
            return self._answer(req, pub, 200, {"settings": settings, "arbitration": {"network": f"{self.net_id:016x}"}})
        if path == "/api/set":
            name, _, value = body.decode().strip().partition("=")
            errors = ""
            if name == "wake_engine" and value == "microwakeword" and not self.models:
                errors = "wake_engine: no microWakeWord model on this Echo yet: add one first\n"
            elif name == "online_updates" and value != "off" and not self.github:
                errors = "online_updates: GitHub cannot be reached from this Echo (timeout), so they stay off\n"
            elif name not in self.settings:
                errors = f"{name}: no such setting on this Echo\n"
            else:
                self.settings[name] = value
            return self._answer(req, pub, 200, {"applied": 0 if errors else 1, "errors": errors, "github_unreachable": ""})
        if path == "/api/mww":
            # mww_list_json: the cutoff as "%.2f", the window, the manifest's phrase as the name
            models = [{"id": i, "name": m["wake_word"], "cutoff": round(m["cutoff"], 2), "window": m["window"],
                       "size": m["size"]} for i, m in sorted(self.models.items())]
            return self._answer(req, pub, 200, {"engine": self.settings["wake_engine"], "active": None, "second": None,
                                                "max": 1 << 20, "models": models})
        if path.startswith("/api/mww/add/"):
            head, _, rest = body.partition(b"\n")
            n = int(head)
            manifest, model = json.loads(rest[:n]), rest[n:]
            if manifest.get("type") != "micro" or not manifest.get("wake_word") or not model:
                return self._answer(req, pub, 400, {"error": "manifest"})
            micro = manifest.get("micro") or {}
            self.manifests_added.append(rest[:n])
            self.models[path.rsplit("/", 1)[1]] = {  # mww_add clamps the cutoff to 0.50-0.99
                "wake_word": manifest["wake_word"], "size": len(model), "model": bytes(model),
                "cutoff": min(max(float(micro.get("probability_cutoff", 0.97)), 0.5), 0.99),
                "window": int(micro.get("sliding_window_size", 5))}
            return self._answer(req, pub, 200, {"ok": True})
        if path.startswith("/api/mww/edit/"):
            model_id = path.rsplit("/", 1)[1]
            if model_id not in self.models:
                return self._answer(req, pub, 400, {"error": "no such model"})
            self.edits.append((model_id, body.decode()))
            fields = dict(line.split("=", 1) for line in body.decode().splitlines() if "=" in line)
            if "cutoff" in fields:
                c = float(fields["cutoff"])
                if not 0.5 - 1e-6 <= c <= 0.99 + 1e-6:
                    return self._answer(req, pub, 400, {"error": "cutoff 0.50 to 0.99"})
                self.models[model_id]["cutoff"] = c
            if "window" in fields:
                self.models[model_id]["window"] = int(fields["window"])
            return self._answer(req, pub, 200, {"ok": True})
        if path == "/api/log/0":
            return self._answer(req, pub, 200, ("\n".join(self.log_lines) + "\n").encode(), text=True)
        if path == "/api/reboot":
            if body != b"reboot":
                return self._answer(req, pub, 400, {"error": "say reboot"})
            self.reboots += 1
            return self._answer(req, pub, 200, {"reboot": True})
        return web.json_response({"error": "not found"}, status=404)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


class FakeScanner:
    def __init__(self, *echos: FakeEcho, ip: str = "127.0.0.1") -> None:
        self.echos, self.ip, self.scans = list(echos), ip, 0
        self.announced: list[bytes] = []

    async def announce(self, packet):
        self.announced.append(packet)

    async def scan(self, own_pub, *, fresh=False, timeout=0.0):
        self.scans += 1
        return [b for b in (hassmic.parse_beacon(e.beacon(), self.ip) for e in self.echos if e.arbitrating) if b]


class FakeStore:
    def __init__(self) -> None:
        self.blobs: dict[str, Any] = {}
        self.kv: dict[str, Any] = {}

    def load_blob(self, name, default=None):
        return self.blobs.get(name, default)

    def save_blob(self, name, value):
        self.blobs[name] = json.loads(json.dumps(value))

    def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    def set_kv(self, key, value):
        self.kv[key] = json.loads(json.dumps(value))


ARB_SERVICE = UserService(name="arbitration_key", key=19, args=[
    UserServiceArg(name="network", type=UserServiceArgType.STRING),
    UserServiceArg(name="key", type=UserServiceArgType.STRING)])
RESTART = ButtonInfo(object_id="restart", key=60, name="Restart")
WEB_UI = TextSensorInfo(object_id="web_ui_address", key=61, name="Web UI address")


class FakeClient:
    """The keyed ESPHome connection: ``arbitration_key`` reaches the fake Echo's ``take_key``."""

    echo: ClassVar[FakeEcho | None] = None
    project: ClassVar[str] = "hassmic.echo-dot-assist"
    services: ClassVar[list[Any]] = [ARB_SERVICE]

    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []
        self.buttons: list[int] = []

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop

    async def device_info_and_list_entities(self):
        info = DeviceInfo(name=NODE, friendly_name="Kitchen Echo", mac_address=MAC, project_name=self.project)
        return info, [RESTART, WEB_UI], list(self.services)

    def subscribe_home_assistant_states_and_services(self, **_kw):
        pass

    async def execute_service(self, svc, data, return_response=None):
        self.executed.append(dict(data))
        if FakeClient.echo is not None and svc.name == "arbitration_key":
            FakeClient.echo.take_key(data["network"], data["key"])

    def button_command(self, key, device_id=0):
        self.buttons.append(key)

    async def disconnect(self, force=False):
        pass


def _models_dir(tmp_path, ids=("hey_jarvis", "okay_nabu")) -> str:
    """A stand-in for ESPHome's model repository: v2 manifests and a few bytes of model each."""
    d = tmp_path / "repo"
    d.mkdir(exist_ok=True)
    for i in ids:
        (d / f"{i}.json").write_text(json.dumps({
            "type": "micro", "wake_word": i.replace("_", " ").title(), "author": "test", "version": 2,
            "trained_languages": ["en"], "model": f"{i}.tflite",
            "micro": {"probability_cutoff": 0.97, "feature_step_size": 10, "sliding_window_size": 5}}))
        (d / f"{i}.tflite").write_bytes(bytes(4) + b"TFL3" + i.encode() * 40)  # the flatbuffer's file identifier
    return str(d)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    echo = FakeEcho()
    await echo.start()
    repo = _models_dir(tmp_path)

    async def fetch(self, url):
        path = os.path.join(repo, url.rsplit("/", 1)[1])
        if not os.path.exists(path):
            raise hassmic.HassmicError(f"{url} answered 404")
        with open(path, "rb") as f:
            return f.read()

    monkeypatch.setattr(hassmic.WakeModelCache, "_fetch", fetch)
    monkeypatch.setattr(hassmic, "WEB_PORT", echo.port)
    monkeypatch.setattr(hassmic, "CONFIRM_S", 1.0)
    monkeypatch.setattr(hassmic, "CONFIRM_POLL_S", 0.01)
    monkeypatch.setattr("services.homehub.drivers.esphome.COMMAND_SETTLE", 0.05)
    FakeClient.echo, FakeClient.project, FakeClient.services = echo, "hassmic.echo-dot-assist", [ARB_SERVICE]
    clients: list[FakeClient] = []

    def build(options=None, entities=None):
        hub, entry = make_hub("esphome", dict(DATA), entities or [], options=options or {})
        hub.store = FakeStore()
        hub.data_dir = str(tmp_path / "data")
        hub._hassmic_scanner = FakeScanner(echo)
        drv = ESPHomeDriver(hub, entry)

        def make(host, port=None):
            clients.append(FakeClient())
            return clients[-1]

        drv._make_client = make  # type: ignore[method-assign]
        hub.drivers[entry.entry_id] = drv
        return hub, drv

    async def up(drv):
        """Connect with the background runner held back, then bring the Echo up in the test's own order."""
        monkeypatch.setattr(hassmic.HassmicManager, "_run", _idle)
        await drv.async_setup()
        mgr = drv.hassmic
        notices: list[dict] = []

        async def notifier(payload):
            notices.append(payload)

        mgr.notifier = notifier
        mgr.notices = notices
        return mgr

    yield type("Env", (), {"echo": echo, "build": staticmethod(build), "up": staticmethod(up), "clients": clients})
    FakeClient.echo = None
    await echo.stop()


async def _idle(self, client):
    return None


def _state(hub, uid_suffix):
    ent = next(e for e in hub.entities.values() if (e.unique_id or "").endswith(f"-{uid_suffix}"))
    return ent, hub.states.get(ent.entity_id)


# -- the whole bring-up ---------------------------------------------------------------------------------------

async def test_bring_up_pushes_the_key_logs_in_and_shows_the_settings(env):
    hub, drv = env.build()
    mgr = await env.up(drv)
    await mgr.bring_up()
    auth = hub._hassmic_authority
    assert env.echo.net_id == auth.net_id == hassmic.NET_ID_START and env.echo.net_key == auth.net_key
    assert env.clients[-1].executed[0]["network"] == "00000000ffffffff" and len(env.clients[-1].executed[0]["key"]) == 140
    assert env.echo.clients[auth.web_pk]["label"] == "HomeHub via homehub"
    _ent, st = _state(hub, "hassmic_wake_engine")
    assert st.state == "Amazon" and st.attributes["options"] == ["Amazon", "microWakeWord"]
    ent, st = _state(hub, "hassmic_online_updates")
    assert st.state == "Off" and ent.entity_id.endswith("_online_updates") and ent.entity_category == "config"
    _ent, st = _state(hub, "hassmic_management")
    assert st.state == "ready" and st.attributes["in_network"] and st.attributes["logged_in"]
    assert st.attributes["network"] == "00000000ffffffff" and "reason" not in st.attributes
    ent, _st = _state(hub, "hassmic_microphone")
    assert ent.entity_category == "diagnostic" and ent.trusted("original_device_class") == "problem"
    # K and HomeHub's keys are kept sealed in the store blob, never in kv, options or entry data
    sealed = hub.store.blobs[hassmic.AUTHORITY_BLOB]
    assert base64.b64decode(sealed["net_key"]) == auth.net_key
    assert sealed["net_key"] not in json.dumps(hub.store.kv) and "net_key" not in json.dumps(drv.entry.options)
    # once the Echo holds K, HomeHub beacons as a quiet member, so the Echo stops offering it K
    [member] = mgr.scanner.announced if mgr.scanner is not None else hassmic.scanner_for(hub).announced
    heard = hassmic.parse_beacon(member, "192.168.10.5")
    assert heard is not None and heard.net == auth.net_id and heard.node == "homehub"
    assert heard.flags == hassmic.F_QUIET and heard.verifies(auth.mac_key)
    await drv.async_unload()


async def test_the_background_runner_brings_the_echo_up(env):
    hub, drv = env.build()
    await drv.async_setup()
    drv.hassmic.mic_check_s = 0.05
    async def settles(key, want):
        for _ in range(200):
            st = hub.states.get(_state(hub, key)[0].entity_id)
            if st is not None and st.state == want:
                return True
            await asyncio.sleep(0.02)
        return False

    assert await settles("hassmic_management", "ready")
    assert await settles("hassmic_microphone", "off")
    await drv.async_unload()


async def test_an_echo_already_in_the_network_is_not_handed_the_key_again(env):
    _hub, drv = env.build()
    mgr = await env.up(drv)
    await mgr.bring_up()
    await mgr.bring_up()
    assert env.echo.keys_taken == 1 and len(env.clients[-1].executed) == 1
    # a reconnect does not log in again either: the approval is remembered
    logins = sum(1 for c in env.echo.calls if c[1] == "/api/vouch/login")
    await mgr.bring_up()
    assert sum(1 for c in env.echo.calls if c[1] == "/api/vouch/login") == logins == 1


async def test_entities_only_for_hassmic_nodes(env):
    FakeClient.project = "example.rgbww"
    hub, drv = env.build()
    await drv.async_setup()
    assert drv.hassmic is None
    assert not [e for e in hub.entities.values() if "hassmic_" in (e.unique_id or "")]
    assert env.echo.calls == [] and env.echo.keys_taken == 0
    await drv.async_unload()


async def test_arbitration_off_is_said_plainly_on_the_device(env):
    FakeClient.services = []  # no arbitration_key: wake_engine=homeassistant at start, or -a 0
    hub, drv = env.build()
    mgr = await env.up(drv)
    with pytest.raises(hassmic.HassmicError) as err:
        await mgr.bring_up()
    mgr._fail(str(err.value))
    _ent, st = _state(hub, "hassmic_management")
    assert st.state == "error" and "not running hassmic's arbitration" in st.attributes["reason"]
    _ent, st = _state(hub, "hassmic_wake_engine")
    assert st.state == "unavailable" and "arbitration" in st.attributes["reason"]
    assert hassmic.AUTHORITY_BLOB not in hub.store.blobs  # nothing made for an Echo that cannot take it
    with pytest.raises(HubError, match="arbitration"):
        await hub.call_service("select", "select_option", {"entity_id": _state(hub, "hassmic_wake_engine")[0].entity_id,
                                                           "option": "microWakeWord"})


async def test_an_approved_echo_with_arbitration_off_keeps_its_settings_reachable(env):
    hub, drv = env.build()
    mgr = await env.up(drv)
    await mgr.bring_up()  # approved while arbitration ran
    env.echo.settings["wake_engine"] = "homeassistant"
    env.echo.arbitrating = False
    FakeClient.services = []
    await drv.async_unload()
    hub2, drv2 = env.build()
    hub2.store = hub.store
    mgr2 = await env.up(drv2)
    drv2._last[(0, WEB_UI.key)] = TextSensorState(key=WEB_UI.key, state=f"http://127.0.0.1:{env.echo.port}")
    await mgr2.bring_up()
    _ent, st = _state(hub2, "hassmic_management")
    assert st.state == "error" and "not running hassmic's arbitration" in st.attributes["reason"]
    _ent, st = _state(hub2, "hassmic_wake_engine")
    assert st.state == "unknown" and "Home Assistant's wake word engine" in st.attributes["reason"]
    await hub2.call_service("select", "select_option", {"entity_id": _ent.entity_id, "option": "Amazon"})
    assert env.echo.settings["wake_engine"] == "amazon"
    await drv2.async_unload()


async def test_no_beacon_is_a_reason_not_a_silence(env):
    hub, drv = env.build()
    hub._hassmic_scanner = FakeScanner()
    mgr = await env.up(drv)
    with pytest.raises(hassmic.HassmicError, match="no arbitration beacon from echo-kitchen"):
        await mgr.bring_up()


async def test_an_echo_in_an_older_network_is_reported(env):
    env.echo.net_id = 5
    _hub, drv = env.build()
    mgr = await env.up(drv)
    with pytest.raises(hassmic.HassmicError, match="older network"):
        await mgr.bring_up()
    assert env.clients[-1].executed == []


async def test_without_a_sealing_store_nothing_is_made(env):
    hub, drv = env.build()
    hub.store = type("NoFernet", (FakeStore,), {"save_blob": lambda self, n, v: (_ for _ in ()).throw(
        RuntimeError("FERNET_KEY is not set"))})()
    mgr = await env.up(drv)
    with pytest.raises(hassmic.HassmicError, match="cannot keep the Echo network key sealed"):
        await mgr.bring_up()
    assert env.echo.keys_taken == 0


# -- the settings API -----------------------------------------------------------------------------------------

async def _logged_in(env):
    hub, drv = env.build()
    mgr = await env.up(drv)
    await mgr.bring_up()
    return hub, drv, mgr


async def test_vouch_is_refused_without_the_network_key(env):
    hub = make_hub("esphome")[0]
    hub.store = FakeStore()
    auth = hassmic.Authority.for_hub(hub)  # HomeHub's K, which this Echo never got
    client = hassmic.EchoWeb(f"http://127.0.0.1:{env.echo.port}", auth)
    with pytest.raises(hassmic.HassmicError, match="refused HomeHub's login voucher"):
        await client.vouch()
    assert sum(1 for c in env.echo.calls if c[1] == "/api/vouch/login") == 2  # one retry, then the reason


async def test_a_reused_counter_is_refused(env, monkeypatch):
    _hub, _drv, mgr = await _logged_in(env)
    monkeypatch.setattr(hassmic, "next_counter", lambda: "1764000000000001")
    status, _body = await mgr.web.call("GET", "/api/state")
    assert status == 200
    with pytest.raises(hassmic.HassmicError, match="does not accept HomeHub's login"):
        await mgr.web.call("GET", "/api/state")


async def test_a_wrong_answer_signature_is_rejected(env):
    _hub, _drv, mgr = await _logged_in(env)
    env.echo.forge = True
    with pytest.raises(hassmic.HassmicError, match="wrong signature"):
        await mgr.web.json("GET", "/api/state")


async def test_a_lost_approval_logs_in_again(env):
    _hub, _drv, mgr = await _logged_in(env)
    env.echo.clients.clear()  # pushed out of clients[], or revoked
    doc = await mgr.web.json("GET", "/api/mww")
    assert doc["models"] == [] and sum(1 for c in env.echo.calls if c[1] == "/api/vouch/login") == 2


async def test_switching_to_microwakeword_installs_the_models_first(env):
    hub, drv, _mgr = await _logged_in(env)
    drv.entry.options["echo_wake_models"] = ["hey_jarvis", "okay_nabu"]
    eid = _state(hub, "hassmic_wake_engine")[0].entity_id
    await hub.call_service("select", "select_option", {"entity_id": eid, "option": "microWakeWord"})
    assert env.echo.settings["wake_engine"] == "microwakeword"
    assert sorted(env.echo.models) == ["hey_jarvis", "okay_nabu"]
    assert hub.states.get(eid).state == "microWakeWord"
    _ent, st = _state(hub, "hassmic_wake_models")
    assert st.state == "hey_jarvis, okay_nabu" and st.attributes["missing"] == []
    # cached once in HomeHub's data dir
    assert sorted(os.listdir(os.path.join(hub.data_dir, "hassmic_wake_models"))) == [
        "hey_jarvis.json", "hey_jarvis.sha256", "hey_jarvis.tflite",
        "okay_nabu.json", "okay_nabu.sha256", "okay_nabu.tflite"]  # each with the digest of its whole download
    await hub.call_service("select", "select_option", {"entity_id": eid, "option": "Amazon"})
    assert env.echo.settings["wake_engine"] == "amazon" and hub.states.get(eid).state == "Amazon"


async def test_a_model_that_cannot_be_had_stops_the_switch_with_its_reason(env):
    hub, drv, _mgr = await _logged_in(env)
    drv.entry.options["echo_wake_models"] = ["hey_jarvis", "no_such_word"]
    eid = _state(hub, "hassmic_wake_engine")[0].entity_id
    with pytest.raises(HubError, match="no_such_word"):
        await hub.call_service("select", "select_option", {"entity_id": eid, "option": "microWakeWord"})
    assert env.echo.settings["wake_engine"] == "amazon"
    _ent, st = _state(hub, "hassmic_wake_models")
    assert "no_such_word" in st.attributes["reason"] and st.attributes["missing"] == ["no_such_word"]


async def test_online_updates_select_and_echo_ota(env):
    sat = {"entity_id": "assist_satellite.kitchen_echo", "unique_id": f"{MAC.upper()}-assist_satellite"}
    hub, drv = env.build(entities=[sat])
    mgr = await env.up(drv)
    hub.store.kv.pop("echo_ota", None)
    await mgr.bring_up()
    eid = _state(hub, "hassmic_online_updates")[0].entity_id
    assert echo_ota.echo_nodes(hub) == [drv.entry.entry_id]
    await echo_ota.ensure_channel(hub)  # what the hub's echo-ota loop (and the bring-up's nudge) runs
    assert env.echo.settings["online_updates"] == "release" and hub.states.get(eid).state == "Release"
    await hub.call_service("select", "select_option", {"entity_id": eid, "option": "Beta"})
    assert env.echo.settings["online_updates"] == "beta"


async def test_online_updates_refused_by_the_echo_shows_why(env):
    hub, _drv, _mgr = await _logged_in(env)
    env.echo.github = False
    eid = _state(hub, "hassmic_online_updates")[0].entity_id
    with pytest.raises(HubError, match="GitHub cannot be reached"):
        await hub.call_service("select", "select_option", {"entity_id": eid, "option": "Release"})
    st = hub.states.get(eid)
    assert st.state == "Off" and "GitHub cannot be reached" in st.attributes["reason"]


# -- the microphone -------------------------------------------------------------------------------------------

async def test_a_dead_microphone_reboots_once_an_hour_and_tells_the_admins(env):
    hub, _drv, mgr = await _logged_in(env)
    now = [1_800_000_000.0]
    mgr.clock = lambda: now[0]
    mic = _state(hub, "hassmic_microphone")[0].entity_id

    assert await mgr.check_microphone() is False and hub.states.get(mic).state == "off"
    env.echo.log_lines += stamped(*[DEAD, REOPEN] * 6)
    assert await mgr.check_microphone() is True
    st = hub.states.get(mic)
    assert st.state == "on" and "micAsr delivers nothing" in st.attributes["reason"] and "rebooted" in st.attributes["reason"]
    assert env.echo.reboots == 1 and mgr.notices == []

    env.echo.log_lines += stamped("hassmic: started", *[DEAD, REOPEN] * 5)  # rebooted, and still deaf
    now[0] += 300
    assert await mgr.check_microphone() is True
    assert env.echo.reboots == 1 and len(mgr.notices) == 1
    notice = mgr.notices[0]
    assert notice["user"] == "@admins" and notice["audience"] == "parents" and "cannot hear" in notice["title"]
    env.echo.log_lines += stamped(DEAD, REOPEN)
    now[0] += 60
    await mgr.check_microphone()
    assert env.echo.reboots == 1 and len(mgr.notices) == 1  # one notice per episode

    env.echo.log_lines += stamped("voice: wake word heard")
    now[0] += 60
    assert await mgr.check_microphone() is False and hub.states.get(mic).state == "off"

    env.echo.log_lines += stamped(*[DEAD, REOPEN] * 4)
    now[0] += 3600
    assert await mgr.check_microphone() is True and env.echo.reboots == 2  # an hour later: the next reboot


async def test_a_hiccup_or_an_old_episode_is_not_a_dead_microphone(env):
    hub, _drv, mgr = await _logged_in(env)
    env.echo.log_lines += stamped(DEAD, REOPEN, "voice: listening")
    assert await mgr.check_microphone() is False
    env.echo.log_lines += stamped(*[DEAD, REOPEN] * 5)
    mgr.clock = lambda: 1_800_000_000.0
    assert await mgr.check_microphone() is True
    # nothing logged since the last look: a dead stream logs every few seconds, so this one recovered
    assert await mgr.check_microphone() is False
    assert hub.states.get(_state(hub, "hassmic_microphone")[0].entity_id).state == "off"


async def test_a_reboot_inside_the_hour_is_not_repeated_but_reported(env):
    _hub, _drv, mgr = await _logged_in(env)
    mgr.clock = lambda: 1_800_000_000.0
    mgr.last_reboot = 1_800_000_000.0 - 600  # rebooted for an earlier episode ten minutes ago
    env.echo.log_lines += stamped(*[DEAD, REOPEN] * 5)
    assert await mgr.check_microphone() is True
    assert env.echo.reboots == 0 and len(mgr.notices) == 1 and "within the last hour" in mgr.notices[0]["body"]


async def test_without_a_login_the_restart_button_reboots(env):
    _hub, _drv, mgr = await _logged_in(env)
    mgr.logged_in = False
    assert await mgr.reboot() == "Restart button"
    assert env.clients[-1].buttons == [RESTART.key]


def test_mic_dead_needs_repeats_at_the_end():
    m = hassmic.HassmicManager.mic_dead
    assert m(["a", DEAD, REOPEN, DEAD, REOPEN, DEAD]) == 3
    assert m([DEAD, REOPEN, DEAD]) == 0
    assert m([DEAD, REOPEN] * 3 + ["x"] * 10) == 0


# -- the authority and options --------------------------------------------------------------------------------

def test_authority_is_made_once_sealed_and_rotates_down():
    hub = make_hub("esphome")[0]
    hub.store = FakeStore()
    auth = hassmic.Authority.for_hub(hub)
    assert auth.net_id == hassmic.NET_ID_START and "net_key" not in repr(auth)
    del hub._hassmic_authority
    again = hassmic.Authority.for_hub(hub)
    assert again.net_key == auth.net_key and again.web_sk == auth.web_sk
    rotated = again.rotate(hub)
    assert rotated.net_id == hassmic.NET_ID_START - 1 and rotated.net_key != auth.net_key
    assert rotated.web_pk == auth.web_pk and hub._hassmic_authority is rotated


def test_no_store_means_no_network_key():
    hub = make_hub("esphome")[0]
    with pytest.raises(hassmic.HassmicError, match="no store"):
        hassmic.Authority.for_hub(hub)


def test_wake_models_option():
    assert hassmic.wake_models_option({}) == ["hey_jarvis", "okay_nabu", "hey_mycroft", "alexa"]
    assert hassmic.wake_models_option({"echo_wake_models": "okay_nabu, alexa,okay_nabu"}) == ["okay_nabu", "alexa"]
    with pytest.raises(hassmic.HassmicError):
        hassmic.wake_models_option({"echo_wake_models": ["../etc"]})


def test_manifest_validation():
    good = {"type": "micro", "wake_word": "Okay Nabu", "micro": {"probability_cutoff": 0.9, "feature_step_size": 10}}
    assert hassmic.validate_manifest("okay_nabu", json.dumps(good).encode())["wake_word"] == "Okay Nabu"
    for bad in ({**good, "type": "nano"}, {**good, "wake_word": ""}, {**good, "micro": {"probability_cutoff": 1.5}},
                {**good, "micro": {"probability_cutoff": 0.9, "feature_step_size": 20}}, {"wake_word": "x"}):
        with pytest.raises(hassmic.HassmicError):
            hassmic.validate_manifest("x", json.dumps(bad).encode())


def test_echo_wake_models_is_an_esphome_option():
    from services.homehub.entries import ENTRY_OPTIONS

    assert "echo_wake_models" in ENTRY_OPTIONS["esphome"]


# -- beacons over a real UDP socket ---------------------------------------------------------------------------

def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_the_scanner_probes_and_hears_answers():
    echo = FakeEcho("echo-den")
    probes: list[bytes] = []

    class Responder(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            probes.append(data)
            self.transport.sendto(echo.beacon(), addr)  # a real Echo broadcasts it to the arbitration port

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(Responder, local_addr=("127.0.0.1", 0))
    try:
        port = transport.get_extra_info("sockname")[1]
        scanner = hassmic.BeaconScanner(bind_port=_free_udp_port(), target=("127.0.0.1", port))
        own = os.urandom(32)
        found = await scanner.scan(own, timeout=0.3)
    finally:
        transport.close()
    assert hassmic.parse_beacon(probes[0]).net == 0 and hassmic.parse_beacon(probes[0]).pub == own
    assert [(b.node, b.pub, b.ip) for b in found] == [("echo-den", echo.arb_pk, "127.0.0.1")]
    assert await scanner.scan(own) == found  # a fresh scan is shared for a few seconds


async def test_an_echo_adopted_unencrypted_is_given_a_key_saved_and_reconnected(monkeypatch):
    """Adoption used to give hassmic its key over plaintext, which it refuses: Echos ended up unencrypted."""
    import logging
    from types import SimpleNamespace

    from services.homehub import flows
    from services.homehub import hassmic as hm

    saved, dropped = [], []
    key = "a2V5LWZvci1hLXRlc3Qtb25seS1ub3QtcmVhbC0wMDA="

    async def provision(host, port, password=""):
        assert (host, port) == ("192.168.10.21", 6053)
        return key

    async def disconnect():
        dropped.append(True)

    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)
    entry = SimpleNamespace(data={"host": "192.168.10.21", "port": 6053, "noise_psk": ""})
    driver = SimpleNamespace(noise_psk=None, host="192.168.10.21", port=6053, password="", entry=entry,
                             device_name="echo-test")
    mgr = hm.HassmicManager.__new__(hm.HassmicManager)
    mgr.driver = driver
    mgr.hub = SimpleNamespace(store=SimpleNamespace(save_config_entry=saved.append))
    mgr.client = SimpleNamespace(disconnect=disconnect)
    mgr.log = logging.getLogger("test")
    assert await mgr.ensure_encryption() is True
    assert driver.noise_psk == key and entry.data["noise_psk"] == key and saved == [entry] and dropped
    assert await mgr.ensure_encryption() is False  # encrypted now: nothing to do


async def test_an_echo_that_refuses_a_key_says_so(monkeypatch):
    import logging
    from types import SimpleNamespace

    from services.homehub import flows
    from services.homehub import hassmic as hm

    async def provision(host, port, password=""):
        return None

    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)
    mgr = hm.HassmicManager.__new__(hm.HassmicManager)
    mgr.driver = SimpleNamespace(noise_psk=None, host="192.168.10.21", port=6053, password="",
                                 entry=SimpleNamespace(data={}), device_name="echo-test")
    mgr.hub, mgr.client, mgr.log = SimpleNamespace(store=None), None, logging.getLogger("test")
    with pytest.raises(hm.HassmicError, match="refused an encryption key"):
        await mgr.ensure_encryption()



async def test_a_model_sent_in_small_chunks_is_read_whole_and_a_short_one_refused(tmp_path):
    """The live download kept only the first chunk: 5512 of 57248 bytes, which the Echo then refused."""
    from aiohttp import web

    model = bytes(4) + b"TFL3" + bytes(range(256)) * 300
    manifest = json.dumps({"type": "micro", "wake_word": "Hey Test", "model": "hey_test.tflite", "version": 2,
                           "micro": {"probability_cutoff": 0.9, "sliding_window_size": 5,
                                     "feature_step_size": 10}}).encode()

    async def handler(request):
        name = request.match_info["name"]
        body = manifest if name.endswith(".json") else model
        resp = web.StreamResponse(headers={"Content-Length": str(len(body))})
        await resp.prepare(request)
        for i in range(0, len(body), 1000):  # many small chunks, as a CDN sends them
            await resp.write(body[i:i + 1000])
        return resp

    app = web.Application()
    app.router.add_get("/m/{name}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        cache = hassmic.WakeModelCache(str(tmp_path / "cache"), base_url=f"http://127.0.0.1:{port}/m")
        got_manifest, got_model = await cache.get("hey_test")
        assert got_model == model and got_manifest == manifest
    finally:
        await runner.cleanup()


async def test_a_truncated_cached_model_is_downloaded_again(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manifest = json.dumps({"type": "micro", "wake_word": "Hey Test", "model": "hey_test.tflite", "version": 2,
                           "micro": {"probability_cutoff": 0.9, "sliding_window_size": 5,
                                     "feature_step_size": 10}}).encode()
    (cache_dir / "hey_test.json").write_bytes(manifest)
    (cache_dir / "hey_test.tflite").write_bytes(b"not a model")
    whole = bytes(4) + b"TFL3" + bytes(200)
    fetched = []

    async def fetch(self, url):
        fetched.append(url)
        return manifest if url.endswith(".json") else whole

    monkeypatch.setattr(hassmic.WakeModelCache, "_fetch", fetch)
    _, model = await hassmic.WakeModelCache(str(cache_dir)).get("hey_test")
    assert model == whole and len(fetched) == 2
    assert not hassmic.is_tflite(b"x" * 100) and hassmic.is_tflite(whole)


async def test_a_cut_off_model_with_a_good_header_is_not_trusted_from_the_cache(tmp_path, monkeypatch):
    """The live case: the first 5512 bytes of a 57248 byte model keep its TFL3 header."""
    whole = bytes(4) + b"TFL3" + bytes(range(256)) * 200
    manifest = json.dumps({"type": "micro", "wake_word": "Hey Test", "model": "hey_test.tflite", "version": 2,
                           "micro": {"probability_cutoff": 0.9, "sliding_window_size": 5,
                                     "feature_step_size": 10}}).encode()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "hey_test.json").write_bytes(manifest)
    (cache_dir / "hey_test.tflite").write_bytes(whole[:5512])  # passes is_tflite, but is not whole
    assert hassmic.is_tflite(whole[:5512])
    fetched = []

    async def fetch(self, url):
        fetched.append(url)
        return manifest if url.endswith(".json") else whole

    monkeypatch.setattr(hassmic.WakeModelCache, "_fetch", fetch)
    cache = hassmic.WakeModelCache(str(cache_dir))
    _, model = await cache.get("hey_test")
    assert model == whole and len(fetched) == 2
    _, again = await cache.get("hey_test")  # now recorded whole: served from the cache
    assert again == whole and len(fetched) == 2
    cache.forget("hey_test")  # the Echo refused it: gone, downloaded next time
    assert not (cache_dir / "hey_test.tflite").exists() and not (cache_dir / "hey_test.sha256").exists()


async def test_a_gzipped_model_download_is_not_mistaken_for_a_short_one(tmp_path):
    """Live: GitHub sent hey_mycroft.json gzipped (264 bytes on the wire, 390 decoded)."""
    import gzip

    from aiohttp import web

    model = bytes(4) + b"TFL3" + bytes(range(256)) * 100
    manifest = json.dumps({"type": "micro", "wake_word": "Hey Test", "model": "hey_test.tflite", "version": 2,
                           "micro": {"probability_cutoff": 0.9, "sliding_window_size": 5,
                                     "feature_step_size": 10}}).encode()
    seen_encodings = []

    async def handler(request):
        seen_encodings.append(request.headers.get("Accept-Encoding"))
        body = manifest if request.match_info["name"].endswith(".json") else model
        packed = gzip.compress(body)
        return web.Response(body=packed, headers={"Content-Encoding": "gzip", "Content-Length": str(len(packed))})

    app = web.Application()
    app.router.add_get("/m/{name}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        got_manifest, got_model = await hassmic.WakeModelCache(
            str(tmp_path / "c"), base_url=f"http://127.0.0.1:{port}/m").get("hey_test")
        assert got_manifest == manifest and got_model == model
        assert set(seen_encodings) == {"identity"}
    finally:
        await runner.cleanup()


# -- wake word sensitivity ------------------------------------------------------------------------------------

def _published(cutoff: float = 0.97, window: int = 5, word: str = "Hey Jarvis") -> bytes:
    """A v2 manifest as ESPHome publishes it (hey_jarvis.json's fields)."""
    return json.dumps({"type": "micro", "wake_word": word, "author": "test", "website": "https://example.invalid/",
                       "model": "hey_jarvis.tflite", "trained_languages": ["en"], "version": 2,
                       "micro": {"probability_cutoff": cutoff, "feature_step_size": 10, "sliding_window_size": window,
                                 "tensor_arena_size": 22860, "minimum_esphome_version": "2024.7.0"}}).encode()


def test_sensitivity_levels_move_the_published_operating_point():
    assert hassmic.tuned(0.97, 5, "standard") == (0.97, 5)
    assert hassmic.tuned(0.97, 5, "more") == (0.94, 5)
    assert hassmic.tuned(0.97, 5, "most") == (0.91, 4)
    assert hassmic.tuned(0.9, 5, "more") == (0.87, 5)  # alexa
    assert hassmic.tuned(0.9, 5, "most") == (0.84, 4)
    assert hassmic.tuned(0.95, 5, "most") == (0.89, 4)  # hey_mycroft
    # never below the floor, and a model published below it is not lowered further
    assert hassmic.tuned(0.82, 5, "most") == (0.80, 4)
    assert hassmic.tuned(0.70, 3, "most") == (0.70, 3)
    assert hassmic.tuned(0.70, 2, "most") == (0.70, 2)
    # what hassmic takes: 0.50 to 0.99, two decimals
    assert hassmic.tuned(0.995, 5, "standard") == (0.99, 5)
    with pytest.raises(hassmic.HassmicError):
        hassmic.tuned(0.97, 5, "loudest")


def test_the_device_manifest_is_the_published_one_with_the_level_applied():
    published = _published()
    for level, (cutoff, window) in {"standard": (0.97, 5), "more": (0.94, 5), "most": (0.91, 4)}.items():
        raw = hassmic.device_manifest("hey_jarvis", published, level)
        doc = hassmic.validate_manifest("hey_jarvis", raw)  # hassmic's own rules still hold
        assert doc["micro"]["probability_cutoff"] == cutoff and doc["micro"]["sliding_window_size"] == window
        assert doc["micro"]["feature_step_size"] == 10 and doc["micro"]["tensor_arena_size"] == 22860
        assert doc["wake_word"] == "Hey Jarvis" and doc["model"] == "hey_jarvis.tflite" and doc["version"] == 2
        assert doc["homehub"] == {"sensitivity": level, "published_probability_cutoff": 0.97,
                                  "published_sliding_window_size": 5}
    # each level is a different manifest (its bytes, so any digest of it, change)
    assert len({hashlib.sha256(hassmic.device_manifest("hey_jarvis", published, lv)).hexdigest()
                for lv in hassmic.SENSITIVITY_OPTIONS}) == 3
    with pytest.raises(hassmic.HassmicError):
        hassmic.device_manifest("hey_jarvis", b"{}", "more")


def test_sensitivity_option_is_checked():
    assert hassmic.sensitivity_option({}) is None
    assert hassmic.sensitivity_option({"echo_wake_sensitivity": "most"}) == "most"
    assert hassmic.sensitivity_option({"echo_wake_sensitivity": "More sensitive"}) == "more"
    with pytest.raises(hassmic.HassmicError, match="loudest"):
        hassmic.sensitivity_option({"echo_wake_sensitivity": "loudest"})


def test_echo_wake_sensitivity_is_an_esphome_option():
    from services.homehub.entries import ENTRY_OPTIONS

    assert "echo_wake_sensitivity" in ENTRY_OPTIONS["esphome"]


def test_wake_detections_are_read_from_hassmic_log_lines():
    lines = [
        "2026-11-20 18:02:11.417Z (private) wake: Hey Jarvis (microwakeword, mean 241/255)",
        "boot+812.004 (private) wake: Alexa (microwakeword, mean 233/255, second wake word)",
        "2026-11-20 18:02:12.000Z voice: listening",
        "2026-11-20 18:02:13.000Z wake: Alexa (amazon)",  # Amazon's engine reports no probability
        "garbage wake: x (microwakeword, mean 9/255)",
    ]
    assert hassmic.parse_wake_detections(lines) == [
        {"at": "2026-11-20 18:02:11.417Z", "wake_word": "Hey Jarvis", "probability": 0.945, "second": False},
        {"at": "boot+812.004", "wake_word": "Alexa", "probability": 0.914, "second": True}]


async def _mww_echo(env, options=None):
    """An Echo on microWakeWord with hey_jarvis and okay_nabu installed at their published values."""
    hub, drv = env.build(options={"echo_wake_models": ["hey_jarvis", "okay_nabu"], **(options or {})})
    mgr = await env.up(drv)
    await mgr.bring_up()
    eid = _state(hub, "hassmic_wake_engine")[0].entity_id
    await hub.call_service("select", "select_option", {"entity_id": eid, "option": "microWakeWord"})
    return hub, drv, mgr


async def test_the_sensitivity_select_tunes_the_installed_models_without_an_upload(env, caplog):
    hub, drv, mgr = await _mww_echo(env)
    ent, st = _state(hub, "hassmic_wake_sensitivity")
    assert ent.entity_category == "config"
    assert st.state == "Standard" and st.attributes["options"] == ["Standard", "More sensitive", "Most sensitive"]
    assert st.attributes["managed"] is False and "accidental wake-ups" in st.attributes["warning"]
    assert env.echo.edits == []  # with no level chosen in HomeHub, nothing on the Echo is touched
    uploads = len(env.echo.manifests_added)

    caplog.set_level("INFO")
    await hub.call_service("select", "select_option", {"entity_id": ent.entity_id, "option": "More sensitive"})
    assert drv.entry.options["echo_wake_sensitivity"] == "more"
    assert {m: (v["cutoff"], v["window"]) for m, v in env.echo.models.items()} == {
        "hey_jarvis": (0.94, 5), "okay_nabu": (0.94, 5)}
    assert len(env.echo.manifests_added) == uploads  # the same .tflite stays: only the manifest's values change
    st = hub.states.get(ent.entity_id)
    assert st.state == "More sensitive" and st.attributes["managed"] is True and "reason" not in st.attributes
    assert st.attributes["models"]["hey_jarvis"] == {"cutoff": 0.94, "window": 5}
    assert "wake word sensitivity changed from not set to More sensitive" in caplog.text
    assert "hey_jarvis set to More sensitive: cutoff 0.97 -> 0.94" in caplog.text

    await hub.call_service("select", "select_option", {"entity_id": ent.entity_id, "option": "Most sensitive"})
    assert env.echo.models["hey_jarvis"]["cutoff"] == 0.91 and env.echo.models["hey_jarvis"]["window"] == 4
    assert "changed from More sensitive to Most sensitive" in caplog.text

    edits = len(env.echo.edits)
    await mgr.bring_up()  # reconciling an Echo already at its level sends nothing
    assert len(env.echo.edits) == edits

    await hub.call_service("select", "select_option", {"entity_id": ent.entity_id, "option": "Standard"})
    assert env.echo.models["hey_jarvis"]["cutoff"] == 0.97 and env.echo.models["hey_jarvis"]["window"] == 5
    # "Hey Jarvis" and the other wanted words are all still there
    assert sorted(env.echo.models) == ["hey_jarvis", "okay_nabu"]


async def test_a_model_installed_later_gets_this_echos_manifest(env):
    hub, drv = env.build(options={"echo_wake_models": ["hey_jarvis"], "echo_wake_sensitivity": "most"})
    mgr = await env.up(drv)
    await mgr.bring_up()
    eid = _state(hub, "hassmic_wake_engine")[0].entity_id
    await hub.call_service("select", "select_option", {"entity_id": eid, "option": "microWakeWord"})
    [manifest] = env.echo.manifests_added
    doc = json.loads(manifest)
    assert doc["micro"]["probability_cutoff"] == 0.91 and doc["micro"]["sliding_window_size"] == 4
    assert doc["homehub"]["sensitivity"] == "most"
    assert env.echo.models["hey_jarvis"]["cutoff"] == 0.91 and env.echo.edits == []  # right the first time
    assert _state(hub, "hassmic_wake_sensitivity")[1].state == "Most sensitive"


async def test_a_reconnect_puts_back_a_level_changed_on_the_echo(env):
    _hub, _drv, mgr = await _mww_echo(env, {"echo_wake_sensitivity": "more"})
    env.echo.models["hey_jarvis"]["cutoff"] = 0.97  # someone used the Echo's own settings page
    await mgr.bring_up()
    assert env.echo.models["hey_jarvis"]["cutoff"] == 0.94


async def test_an_unknown_sensitivity_is_refused_and_nothing_is_saved(env):
    hub, drv, _mgr = await _mww_echo(env)
    eid = _state(hub, "hassmic_wake_sensitivity")[0].entity_id
    with pytest.raises(HubError, match="not a wake word sensitivity"):
        await hub.call_service("select", "select_option", {"entity_id": eid, "option": "Loudest"})
    assert "echo_wake_sensitivity" not in drv.entry.options and env.echo.edits == []


async def test_a_refused_edit_is_shown_on_the_select(env):
    hub, _drv, _mgr = await _mww_echo(env)
    del env.echo.models["okay_nabu"]  # gone from the Echo after HomeHub listed it
    eid = _state(hub, "hassmic_wake_sensitivity")[0].entity_id
    with pytest.raises(HubError, match="okay_nabu"):
        await hub.call_service("select", "select_option", {"entity_id": eid, "option": "More sensitive"})
    assert "okay_nabu" in hub.states.get(eid).attributes["reason"]


async def test_recent_wake_detections_are_shown_with_their_probability(env):
    hub, _drv, mgr = await _mww_echo(env, {"echo_wake_sensitivity": "more"})
    env.echo.log_lines += stamped("(private) wake: Hey Jarvis (microwakeword, mean 243/255)", "voice: listening",
                                  "(private) wake: Okay Nabu (microwakeword, mean 251/255)")
    await mgr.check_microphone()
    await mgr.check_microphone()  # the same lines read again are not counted twice
    ent, st = _state(hub, "hassmic_wake_detections")
    assert ent.entity_category == "diagnostic" and st.state == "2"
    newest, oldest = st.attributes["detections"]
    assert oldest["wake_word"] == "Hey Jarvis" and oldest["probability"] == 0.953 and oldest["model"] == "hey_jarvis"
    assert oldest["cutoff"] == 0.94 and newest["wake_word"] == "Okay Nabu" and st.attributes["last"] == newest
    # kept with the Echo's state, so a HomeHub restart keeps them
    assert len(hub.store.kv[mgr._kv_key]["wake_detections"]) == 2
