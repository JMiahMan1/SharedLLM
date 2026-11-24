"""The pin finder against a made-up lamp, and templates measured in the household.

The lamp below is a Tasmota device whose LEDs are wired to some GPIO pins (``wiring``). It runs whatever
template it is given, works out how hard each pin is driven (PWM channels, ``SetOption68`` independent
channels, ``SetOption92`` brightness + CT, inverted PWMi pins) and says what a person would see. The "person"
answering the pin finder's questions only looks at the lamp; the expected templates are written out by hand.
Every name, address and MAC is made up.
"""
from __future__ import annotations

import asyncio
import colorsys
import json
import logging
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from services.homehub import tasmota_measured as tm
from services.homehub import tasmota_pin_finder as pf
from services.homehub import tasmota_templates as tt
from services.homehub import tasmota_templates_api
from services.homehub.auth import Auth
from services.homehub.tests.test_tasmota_templates import GENERIC, SECRET, TemplateTasmota, hub_with, kid_token

#: What the household worked out by hand for this made-up model, written out in full.
LOTTON = {"NAME": "Lotton A19 CCT", "GPIO": [0, 0, 0, 0, 0, 416, 0, 0, 0, 449, 0, 0, 0, 0], "FLAG": 0, "BASE": 18,
          "CMND": "SO92 1"}
KAUF_PINS = [0, 0, 0, 0, 416, 419, 0, 0, 417, 420, 418, 0, 0, 0]
ESP8266_PINS = [0, 1, 2, 3, 4, 5, 9, 10, 12, 13, 14, 15, 16, 17]

WIRING = {
    "lotton": {5: "bright", 13: "ct_cool"},     # brightness pin, CT pin that cools when driven
    "ct_plain": {4: "bright", 12: "ct_warm"},   # brightness pin, CT pin that warms when driven
    "cw": {12: "cold", 14: "warm"},
    "kauf": {4: "red", 12: "green", 14: "blue", 5: "cold", 13: "warm"},
    "rgb": {14: "red", 4: "green", 12: "blue"},
    "dimmer": {13: "bright"},
    "chip": {},                                 # LEDs behind an SM2135-style driver: no PWM pin lights them
    "far": {0: "cold", 2: "warm"},              # wired to pins bulbs rarely use
    "inverted_led": {4: "bright_inverted"},
}


class Lamp(TemplateTasmota):
    def __init__(self, wiring: str = "lotton", *, ct_backwards: bool = False, **kw: Any) -> None:
        super().__init__(name="Den Lamp", hostname="tasmota-0A01", **kw)
        self.wiring = dict(WIRING[wiring])
        self.so68, self.so92 = 0, 0
        self.ch_val: dict[int, int] = {}
        self.ch_pow: dict[int, bool] = {}
        self.power, self.dimmer, self.ct, self.hsb, self.white_mode = False, 100, 300, (0, 0, 100), True
        self.ct_backwards = ct_backwards  # a firmware whose CT mode runs the other way, to exercise the fix
        self.views: list[Any] = []

    # -- what the pins do ---------------------------------------------------------------------------------
    def _pwm(self) -> list[tuple[int, int, bool]]:
        """``(gpio, channel 0-based, inverted)`` for each PWM pin of the running template."""
        if self.module != 0:
            return []
        out = []
        for idx, code in enumerate(self.template["GPIO"]):
            if 416 <= code < 432:
                out.append((ESP8266_PINS[idx], code - 416, False))
            elif 448 <= code < 464:
                out.append((ESP8266_PINS[idx], code - 448, True))
        return out

    def _channels(self, n: int) -> list[float]:
        if self.so68:
            return [self.ch_val.get(c, 0) if self.ch_pow.get(c) else 0 for c in range(n)]
        if not self.power:
            return [0.0] * n
        d = float(self.dimmer)
        warm = (self.ct - 153) / 347
        if self.ct_backwards:
            warm = 1 - warm
        if n == 1:
            return [d]
        if n == 2:
            return [d, warm * 100] if self.so92 else [d * (1 - warm), d * warm]
        r, g, b = (x * 100 for x in colorsys.hsv_to_rgb(self.hsb[0] / 360, self.hsb[1] / 100, d / 100))
        if n == 3:
            return [r, g, b]
        if n == 4:
            return [0, 0, 0, d] if self.white_mode else [r, g, b, 0]
        return [0, 0, 0, d * (1 - warm), d * warm] if self.white_mode else [r, g, b, 0, 0]

    def duties(self) -> dict[int, float]:
        pwm = self._pwm()
        chans = self._channels(len(pwm))
        return {gpio: (100 - chans[ch]) if inv else chans[ch] for gpio, ch, inv in pwm}

    def look(self) -> Any:
        """``"dark"``, a colour, ``"other"``, or ``("white", tone)`` with tone 1 the coolest."""
        duty = self.duties()
        on = {role: duty.get(gpio, 0) for gpio, role in self.wiring.items()}
        colours = [r for r in ("red", "green", "blue") if on.get(r, 0) > 0]
        cold, warm = on.get("cold", 0), on.get("warm", 0)
        tone = None
        if cold or warm:
            tone = cold / (cold + warm)
        if on.get("bright", 0) > 0:
            if "ct_cool" in on:
                tone = 0.2 + 0.8 * on["ct_cool"] / 100
            elif "ct_warm" in on:
                tone = 1.0 - 0.8 * on["ct_warm"] / 100
            else:
                tone = 0.5
        if "bright_inverted" in on and on["bright_inverted"] < 100:
            tone = 0.5
        if colours and tone is not None:
            return "other"
        if colours:
            return colours[0] if len(colours) == 1 else "other"
        return ("white", tone) if tone is not None else "dark"

    # -- commands ------------------------------------------------------------------------------------------
    def run(self, command: str) -> dict[str, Any]:
        name, _, arg = command.partition(" ")
        low = name.lower()
        if low == "backlog":
            self.commands.append(command)
            self.views = []
            for part in arg.split(";"):
                self.run(part.strip())
            self.views.append(self.look())
            return {}
        if low == "delay":
            self.views.append(self.look())
            return {"Delay": arg}
        if low.startswith(("setoption", "so")):
            self.commands.append(command)
            num = int(low.removeprefix("setoption").removeprefix("so"))
            if arg:
                setattr(self, f"so{num}", 1 if arg.strip().upper() in ("1", "ON") else 0)
            return {f"SetOption{num}": "ON" if getattr(self, f"so{num}", 0) else "OFF"}
        if low == "restart":
            self.commands.append(command)
            self.down_until = time.monotonic() + self.down_s
            self.sts["UptimeSec"] = 4
            return {"Restart": "Restarting"}
        if low.startswith("channel"):
            self.commands.append(command)
            ch = int(low[7:]) - 1
            self.ch_val[ch] = int(arg)
            if int(arg) > 0:
                self.ch_pow[ch] = True
            return {f"Channel{ch + 1}": int(arg)}
        if low.startswith("power"):
            self.commands.append(command)
            on = arg.strip().upper() == "ON"
            if self.so68 and low[5:]:
                self.ch_pow[int(low[5:]) - 1] = on
            else:
                self.power = on
            return {"POWER": "ON" if on else "OFF"}
        if low in ("dimmer", "ct", "hsbcolor", "white"):
            self.commands.append(command)
            self.power = True
            if low == "dimmer":
                self.dimmer = int(arg)
            elif low == "ct":
                self.ct, self.white_mode = int(arg), True
            elif low == "white":
                self.dimmer, self.white_mode = int(arg), True
            else:
                h, sat, b = (int(x) for x in arg.split(","))
                self.hsb, self.dimmer, self.white_mode = (h, sat, b), b, False
            return {"POWER": "ON"}
        return super().run(command)


# -- the person ------------------------------------------------------------------------------------------------

def _word(look: Any) -> str:
    if look == "dark" or look == "other" or isinstance(look, str):
        return look
    tone = look[1]
    return "cool" if tone >= 0.66 else "warm" if tone <= 0.34 else "white"


def person(dev: Lamp, q: dict[str, Any], *, rest: str = "rest", say: dict[int, str] | None = None) -> str:
    """What someone looking at the lamp answers."""
    values = [a["value"] for a in q["answers"]]
    seen = dev.look()
    if q["id"] == "baseline":
        return "dark" if seen == "dark" else "lit"
    if q["id"] == "single":
        if say and q["pin"] in say:
            return say[q["pin"]]
        return _word(seen)
    if q["id"] in ("pair", "compare"):
        first, second = dev.views[0], dev.views[-1]
        if second == "dark":
            return "dark"
        t1, t2 = first[1], second[1]
        if q["id"] == "compare":
            return "first" if t1 > t2 + 0.1 else "second" if t2 > t1 + 0.1 else "same"
        return "cooler" if t2 > t1 + 0.1 else "warmer" if t2 < t1 - 0.1 else "same"
    if q["id"] == "offer_rest":
        return rest
    if "on" in values:
        return "off" if seen == "dark" else "on"
    if "red" in values:
        return seen if seen in ("red", "green", "blue") else "white" if isinstance(seen, tuple) else "other"
    if values == ["white", "other"]:
        return "white" if isinstance(seen, tuple) else "other"
    word = _word(seen)
    return word if word in ("cool", "warm") else "other"


async def run_finder(runtime, dev: Lamp, *, maker: str = "Lotton", model: str = "A19 CCT", limit: int = 40,
                     **kw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out = await pf.start(runtime, "t1", {"confirm": True, "manufacturer": maker, "model": model})
    asked = []
    while out.get("question") and len(asked) < limit:
        q = out["question"]
        asked.append(q)
        out = await pf.answer(runtime, "t1", {"step": q["step"], "answer": person(dev, q, **kw)})
    return out, asked


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(tt, "RESTART_GRACE_S", 0.05)
    monkeypatch.setattr(tt, "RESTART_POLL_S", 0.05)
    monkeypatch.setattr(tt, "RESTART_WAIT_S", 2.0)
    yield
    for s in list(pf._sessions.values()):
        if s.idle is not None:
            s.idle.cancel()
    pf._sessions.clear()
    tt.BUSY.clear()


@pytest.fixture
async def lamp():
    devices: list[Lamp] = []

    async def make(wiring: str = "lotton", **kw: Any) -> tuple[Lamp, str]:
        dev = Lamp(wiring, **kw)
        devices.append(dev)
        return dev, await dev.start()

    yield make
    for dev in devices:
        await dev.stop()


def restarts(dev: Lamp) -> int:
    return sum(1 for c in dev.commands if c.startswith("Backlog Template") or c == "Restart 1")


# -- what it finds ---------------------------------------------------------------------------------------------

async def test_it_finds_the_lotton_bulb_as_it_was_found_by_hand(lamp, tmp_path):
    dev, host = await lamp("lotton")
    hub, runtime, entry = hub_with(tmp_path, host, title="Den Lamp")
    out, asked = await run_finder(runtime, dev)
    assert out["status"] == "done", out["message"]
    assert json.loads(out["result"]["template"]) == LOTTON
    assert out["result"]["commands"] == ["SO92 1"] and out["result"]["type"] == "ct" and out["result"]["invert"]
    # It ran the template it found, in CT mode, with the probe's option put back.
    assert dev.module == 0 and dev.template["GPIO"] == LOTTON["GPIO"] and dev.so92 == 1 and dev.so68 == 0
    assert restarts(dev) == 2  # the probe, then the template
    ids = [q["id"] for q in asked]
    assert ids[:6] == ["baseline", "single", "single", "single", "single", "single"]
    assert "pair" in ids and ids[-2:] == ["verify", "verify"]
    assert [q["text"] for q in asked[-2:]] == ["Is it a cool, bluish white now?", "And now, is it a warm, yellowish white?"]
    # The first probe tried the pins bulbs use, as PWM1..5, with independent channels.
    probe = next(c for c in dev.commands if c.startswith("Backlog Template") and "pin finder" in c)
    assert '"GPIO":[0,0,0,0,416,417,0,0,418,419,420,0,0,0]' in probe
    # Nothing is left to put back; the template it replaced is what "Restore previous" sends.
    assert "pin_finder_backup" not in entry.data and entry.data["previous_template"] == tt.template_text(GENERIC)
    assert entry.data["pin_finder_result"]["kind"] == "bulb"
    assert pf.state(runtime, "t1")["status"] == "done" and "t1" not in tt.BUSY
    assert any(e.unique_id.endswith("_light_light_0") for e in hub.entities.values())


async def test_a_person_who_calls_the_brightness_pin_plain_white_gets_the_same(lamp, tmp_path):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, _asked = await run_finder(runtime, dev, say={5: "white"})
    assert json.loads(out["result"]["template"]) == LOTTON


async def test_a_ct_pin_that_warms_is_not_inverted(lamp, tmp_path):
    dev, host = await lamp("ct_plain")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, _asked = await run_finder(runtime, dev, maker="", model="Porch")
    gpio = json.loads(out["result"]["template"])["GPIO"]
    assert gpio == [0, 0, 0, 0, 416, 0, 0, 0, 417, 0, 0, 0, 0, 0] and not out["result"]["invert"]
    assert json.loads(out["result"]["template"])["NAME"] == "Porch"


async def test_cold_and_warm_pins(lamp, tmp_path):
    dev, host = await lamp("cw")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev)
    assert json.loads(out["result"]["template"])["GPIO"] == [0, 0, 0, 0, 0, 0, 0, 0, 416, 0, 417, 0, 0, 0]
    assert out["result"]["commands"] == [] and "pair" not in [q["id"] for q in asked]
    assert dev.so92 == 0


async def test_two_whites_nobody_could_tell_apart_are_compared(lamp, tmp_path):
    dev, host = await lamp("cw")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev, say={12: "white", 14: "white"})
    assert "compare" in [q["id"] for q in asked]
    assert json.loads(out["result"]["template"])["GPIO"] == [0, 0, 0, 0, 0, 0, 0, 0, 416, 0, 417, 0, 0, 0]


async def test_an_rgbcw_bulb_comes_out_as_the_catalog_has_it(lamp, tmp_path):
    dev, host = await lamp("kauf")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev, maker="KAUF", model="BLF10")
    assert json.loads(out["result"]["template"])["GPIO"] == KAUF_PINS
    assert [q["text"] for q in asked if q["id"] == "verify"][:3] == ["What colour is it now?"] * 3


async def test_an_rgb_bulb_and_a_dimmable_white(lamp, tmp_path):
    dev, host = await lamp("rgb")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, _ = await run_finder(runtime, dev)
    assert json.loads(out["result"]["template"])["GPIO"] == [0, 0, 0, 0, 417, 0, 0, 0, 418, 0, 416, 0, 0, 0]
    (tmp_path / "d").mkdir()
    dev2, host2 = await lamp("dimmer")
    _hub, runtime2, _entry = hub_with(tmp_path / "d", host2)
    out, asked = await run_finder(runtime2, dev2, rest="dimmer")
    assert out["status"] == "done" and out["result"]["type"] == "dimmer"
    assert json.loads(out["result"]["template"])["GPIO"] == [0, 0, 0, 0, 0, 0, 0, 0, 0, 416, 0, 0, 0, 0]
    assert any(q["id"] == "offer_rest" for q in asked)


async def test_pins_bulbs_rarely_use_are_tried_when_asked(lamp, tmp_path):
    dev, host = await lamp("far")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev)
    assert out["status"] == "done", out["message"]
    gpio = json.loads(out["result"]["template"])["GPIO"]
    assert gpio[0] == 416 and gpio[2] == 417 and sum(1 for g in gpio if g) == 2
    assert [q["id"] for q in asked].count("baseline") == 2 and restarts(dev) == 3


async def test_a_wrong_ct_direction_is_put_right_after_the_check(lamp, tmp_path):
    dev, host = await lamp("lotton", ct_backwards=True)
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev)
    assert out["status"] == "done"
    assert json.loads(out["result"]["template"])["GPIO"] == [0, 0, 0, 0, 0, 416, 0, 0, 0, 417, 0, 0, 0, 0]
    assert [q["id"] for q in asked].count("verify") == 3 and restarts(dev) == 3


# -- it never leaves a device half done ------------------------------------------------------------------------

async def test_a_bulb_no_pin_lights_is_put_back_as_it_was(lamp, tmp_path):
    dev, host = await lamp("chip")
    _hub, runtime, entry = hub_with(tmp_path, host)
    dev.so68 = 0
    out, _asked = await run_finder(runtime, dev)
    assert out["status"] == "failed" and out["restored"] and "SM2135" in out["message"]
    assert "Put back as it was (Generic)" in out["message"]
    assert dev.template == GENERIC and dev.module == 18 and dev.so68 == 0 and dev.so92 == 0
    assert "pin_finder_backup" not in entry.data and "t1" not in tt.BUSY


async def test_the_saved_template_is_kept_before_anything_is_sent(lamp, tmp_path, monkeypatch):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    seen: list[Any] = []
    real = dev.run

    def watch(command: str) -> dict[str, Any]:
        if command.startswith(("SetOption92 0", "Backlog Template")) and not seen:
            saved = next(e for e in runtime.store.load_config_entries() if e.entry_id == "t1")
            seen.append(saved.data.get("pin_finder_backup"))
        return real(command)

    monkeypatch.setattr(dev, "run", watch)
    await pf.start(runtime, "t1", {"confirm": True})
    assert seen[0]["template"] == tt.template_text(GENERIC) and seen[0]["module"] == 18 and seen[0]["so68"] == 0


async def test_cancel_puts_it_back(lamp, tmp_path):
    dev, host = await lamp("lotton")
    dev.so92 = 1
    _hub, runtime, entry = hub_with(tmp_path, host)
    out = await pf.start(runtime, "t1", {"confirm": True})
    assert out["question"]["id"] == "baseline" and dev.so68 == 1 and dev.so92 == 0
    with pytest.raises(tt.TemplateError) as err:
        await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    assert err.value.status == 409 and "pin finder" in str(err.value)
    with pytest.raises(pf.FinderError, match="confirm"):
        await pf.cancel(runtime, "t1", {})
    out = await pf.cancel(runtime, "t1", {"confirm": True})
    assert out["status"] == "cancelled" and out["restored"] and not out["question"]
    assert dev.template == GENERIC and dev.module == 18 and dev.so68 == 0 and dev.so92 == 1
    assert "pin_finder_backup" not in entry.data and "t1" not in tt.BUSY


async def test_a_lamp_lit_with_everything_off_stops_and_is_put_back(lamp, tmp_path):
    dev, host = await lamp("inverted_led")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out, asked = await run_finder(runtime, dev)
    assert [q["id"] for q in asked] == ["baseline"] and out["status"] == "failed" and out["restored"]
    assert "wired the other way round" in out["message"] and dev.template == GENERIC


async def test_a_device_that_does_not_come_back_keeps_its_saved_template_for_later(lamp, tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "RESTART_WAIT_S", 0.3)
    dev, host = await lamp("lotton")
    _hub, runtime, entry = hub_with(tmp_path, host)
    dev.down_s = 30
    out = await pf.start(runtime, "t1", {"confirm": True})
    assert out["status"] == "failed" and not out["restored"] and out["interrupted"]
    assert entry.data["pin_finder_backup"]["template"] == tt.template_text(GENERIC)
    state = pf.state(runtime, "t1")
    assert state["interrupted"] and "can be put back" in state["message"]
    with pytest.raises(pf.FinderError) as err:
        await pf.start(runtime, "t1", {"confirm": True})
    assert err.value.status == 409
    dev.down_until = 0.0
    dev.down_s = 0.2
    pf._sessions.clear()  # as after a hub restart
    tt.BUSY.clear()
    with pytest.raises(tt.TemplateError, match="did not finish") as err:
        await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    assert err.value.status == 409
    out = await pf.cancel(runtime, "t1", {"confirm": True})
    assert out["restored"] and dev.template == GENERIC and dev.module == 18
    assert "pin_finder_backup" not in entry.data


async def test_an_abandoned_run_is_put_back(lamp, tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "IDLE_RESTORE_S", 0.2)
    dev, host = await lamp("lotton")
    _hub, runtime, entry = hub_with(tmp_path, host)
    await pf.start(runtime, "t1", {"confirm": True})
    for _ in range(60):
        await asyncio.sleep(0.1)
        if "pin_finder_backup" not in entry.data:
            break
    assert dev.template == GENERIC and "pin_finder_backup" not in entry.data
    assert pf.state(runtime, "t1")["status"] == "cancelled" and "Nobody answered" in pf.state(runtime, "t1")["message"]


async def test_answers_are_checked(lamp, tmp_path):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    with pytest.raises(pf.FinderError, match="confirm"):
        await pf.start(runtime, "t1", {})
    with pytest.raises(pf.FinderError, match="lights"):
        await pf.start(runtime, "t1", {"confirm": True, "kind": "plug"})
    assert dev.commands == []
    with pytest.raises(pf.FinderError) as err:
        await pf.answer(runtime, "t1", {"step": 1, "answer": "dark"})
    assert err.value.status == 409
    out = await pf.start(runtime, "t1", {"confirm": True})
    step = out["question"]["step"]
    with pytest.raises(pf.FinderError) as err:
        await pf.answer(runtime, "t1", {"step": step - 1, "answer": "dark"})
    assert err.value.status == 409 and "earlier" in str(err.value)
    with pytest.raises(pf.FinderError) as err:
        await pf.answer(runtime, "t1", {"step": step, "answer": "purple"})
    assert err.value.status == 400
    with pytest.raises(pf.FinderError) as err:
        await pf.start(runtime, "t1", {"confirm": True})
    assert err.value.status == 409
    out = await pf.answer(runtime, "t1", {"step": step, "answer": "dark"})
    shown = dev.commands[-1]
    again = await pf.answer(runtime, "t1", {"step": out["question"]["step"], "answer": "again"})
    assert again["question"]["step"] == out["question"]["step"] and dev.commands[-1] == shown
    await pf.cancel(runtime, "t1", {"confirm": True})


async def test_the_password_never_reaches_the_log(lamp, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    dev, host = await lamp("lotton", password="made-up-pw-31")
    _hub, runtime, _entry = hub_with(tmp_path, host, data={"username": "admin", "password": "made-up-pw-31"})
    out, _asked = await run_finder(runtime, dev)
    assert out["status"] == "done" and set(dev.auth_seen) == {"made-up-pw-31"}
    # The made-up device's own web server logs its requests; everything the hub logs is checked.
    hub_lines = [r.getMessage() for r in caplog.records if not r.name.startswith("aiohttp.access")]
    assert hub_lines and not any("made-up-pw-31" in line for line in hub_lines)


def test_probe_templates_put_pins_where_tasmota_reads_them():
    assert pf.probe_template("esp8266", [4, 5, 12, 13, 14])["GPIO"] == [0, 0, 0, 0, 416, 417, 0, 0, 418, 419, 420,
                                                                        0, 0, 0]
    esp32 = pf.probe_template("esp32", [26])["GPIO"]
    assert len(esp32) == 36 and esp32.index(416) == 22  # GPIO26 sits where the Shelly Plus 1's relay does
    assert len(pf.probe_template("esp32c3", [3])["GPIO"]) == 22
    assert pf.candidate_pins("esp8266", "ESP8285N08")[1][-2:] == [9, 10]
    assert 9 not in pf.candidate_pins("esp8266", "ESP8266EX")[1]
    for family in pf.COMMON:
        assert not set(pf.COMMON[family]) & set(pf.REST[family])


# -- measured templates ----------------------------------------------------------------------------------------

async def test_the_found_template_is_saved_as_measured_on_the_device(lamp, tmp_path):
    dev, host = await lamp("lotton")
    hub, runtime, _entry = hub_with(tmp_path, host, title="Den Lamp")
    hub.devices["dev1"].name_by_user = "Reading nook lamp"
    await run_finder(runtime, dev)
    saved = await tm.save_from_device(runtime, "t1", {"manufacturer": "Lotton", "model": "A19 CCT"})
    rec = saved["template"]
    assert rec["method"] == "pin_finder" and rec["measured_on"] == "Reading nook lamp" and rec["kind"] == "bulb"
    assert rec["commands"] == ["SO92 1"] and "pin finder" in rec["how"] and json.loads(rec["template"]) == LOTTON
    assert "Measured on Reading nook lamp" in saved["message"]
    again = await tm.save_from_device(runtime, "t1", {"manufacturer": "Lotton", "model": "A19 CCT"})
    assert again["template"]["id"] == rec["id"] and len(runtime.store.load_measured_templates()) == 1


async def test_a_device_without_a_template_of_its_own_cannot_be_saved(lamp, tmp_path):
    _dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    with pytest.raises(tt.TemplateError) as err:
        await tm.save_from_device(runtime, "t1", {"model": "X1"})
    assert err.value.status == 409 and "Generic" in str(err.value)


def _seed(_runtime: Any, **over: Any) -> dict[str, Any]:
    body = {"device_name": "den lamp", "manufacturer": "Lotton", "model": "A19 CCT", "kind": "bulb",
            "template": json.dumps(LOTTON, separators=(",", ":")), "commands": ["SO92 1"],
            "how": "Worked out by hand: one PWM pin at a time, checked by eye.", "measured_at": "2026-11-24", **over}
    return body


async def test_a_template_worked_out_by_hand_is_recorded_without_touching_the_device(lamp, tmp_path):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host, title="Den Lamp")
    out = await tm.save_given(runtime, _seed(runtime))
    rec = out["template"]
    assert rec["method"] == "by_hand" and rec["measured_on"] == "Den Lamp" and rec["measured_at"] == "2026-11-24"
    assert rec["chip"] == "ESP8266" and json.loads(rec["template"]) == LOTTON and dev.commands == []
    # Running it again replaces it rather than adding a copy.
    await tm.save_given(runtime, _seed(runtime))
    assert len(runtime.store.load_measured_templates()) == 1
    for bad, says in (({"device_name": "Nope"}, "No Tasmota device"), ({"model": ""}, "model"),
                      ({"kind": "toaster"}, "kind"), ({"template": "{oops"}, "not valid JSON"),
                      ({"commands": ["a; b"]}, "';'"),
                      ({"template": '{"NAME":"X","GPIO":[0,0,0,0,0,0,0,0,0,0,0,0,0,0],"FLAG":0,"BASE":18}'}, "no pins")):
        with pytest.raises(tt.TemplateError, match=says):
            await tm.save_given(runtime, _seed(runtime, **bad))


def facts(**kw: Any) -> dict[str, Any]:
    base = {"device_name": None, "friendly_name": [], "hostname": None, "chip": "ESP8266EX", "module": 18,
            "module_name": "Generic", "template": GENERIC, "template_name": "Generic", "light": False,
            "light_channels": 0, "relays": 0, "energy": False, "other_names": []}
    return {**base, **kw}


def lotton_record(device_name: str = "Den Lamp") -> dict[str, Any]:
    return tm.make_record(manufacturer="Lotton", model="A19 CCT", kind="bulb", chip="ESP8266EX",
                          template=LOTTON, commands=["SO92 1"], method="by_hand", device_entry_id="t9",
                          device_name=device_name)


def test_a_measured_template_ranks_first_when_the_model_matches():
    rec = lotton_record()
    out = tt.suggest(facts(device_name="Lotton A19 CCT bedroom"), measured=[rec])
    assert out[0]["id"] == rec["id"] and out[0]["source"] == "measured" and out[0]["measured_on"] == "Den Lamp"
    assert out[0]["reasons"][0] == "Its name matches the model measured on Den Lamp"
    assert all(s["source"] == "catalog" for s in out[1:])
    # A KAUF-named device still gets KAUF first; the measured bulb is not pushed on it.
    kauf = tt.suggest(facts(device_name="Kauf Bulb"), measured=[rec])
    assert kauf[0]["id"].startswith("kauf-")
    # Another chip family never sees it.
    other_chip = tt.suggest(facts(device_name="Lotton A19 CCT", chip="ESP32-D0WD"), measured=[rec])
    assert all(s["id"] != rec["id"] for s in other_chip)


async def test_a_device_that_says_nothing_about_itself_is_offered_the_pin_finder_first(lamp, tmp_path):
    _dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host, title="Den-Lamp")
    out = await tt.read_device(runtime, "t1")
    assert out["identified"] is False and out["suggestions"] == []
    assert "nothing that identifies it" in out["identify_note"] and "pin finder" in out["identify_note"]
    # The bulb templates it would have ranked first are still there, as guesses.
    assert out["guesses"] and out["guesses"][0]["kind"] == "bulb"


async def test_a_device_whose_name_names_a_model_is_identified(lamp, tmp_path):
    _dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host, title="Lotton A19 CCT")
    runtime.store.save_measured_template(lotton_record())
    out = await tt.read_device(runtime, "t1")
    assert out["identified"] and out["guesses"] == []
    assert out["suggestions"][0]["source"] == "measured"


async def test_a_measured_template_is_applied_with_its_commands_and_recognised(lamp, tmp_path):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    rec = lotton_record()
    runtime.store.save_measured_template(rec)
    with pytest.raises(tt.TemplateError, match="No such measured"):
        await tt.apply_template(runtime, "t1", {"measured_id": "measured-nope", "confirm": True})
    out = await tt.apply_template(runtime, "t1", {"measured_id": rec["id"], "confirm": True})
    assert out["verified"] and dev.so92 == 1
    assert dev.commands.index("SO92 1") < dev.commands.index(out["sent"])
    got = await tt.read_device(runtime, "t1")
    assert got["up_to_date"] and got["current"]["measured_matches"][0]["measured_on"] == "Den Lamp"
    assert "measured on Den Lamp" in got["message"]


# -- the API ---------------------------------------------------------------------------------------------------

async def test_the_api_is_for_admins(lamp, tmp_path):
    dev, host = await lamp("lotton")
    _hub, runtime, _entry = hub_with(tmp_path, host, title="Den Lamp")
    app = FastAPI()
    app.include_router(tasmota_templates_api.build_router(runtime, Auth(runtime.store, SECRET)))
    admin = {"Authorization": f"Bearer {SECRET}"}
    kid = {"Authorization": f"Bearer {kid_token(runtime.store)}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as c:
        routes = (("get", "/hub/tasmota/t1/pinfinder"), ("post", "/hub/tasmota/t1/pinfinder/start"),
                  ("post", "/hub/tasmota/t1/pinfinder/answer"), ("post", "/hub/tasmota/t1/pinfinder/cancel"),
                  ("post", "/hub/tasmota/t1/measured"), ("post", "/hub/tasmota/measured"),
                  ("delete", "/hub/tasmota/measured/measured-x"))
        for verb, path in routes:
            kw = {"json": {"confirm": True}} if verb == "post" else {}
            assert (await getattr(c, verb)(path, headers=kid, **kw)).status_code == 401
            assert (await getattr(c, verb)(path, **kw)).status_code == 401
        assert dev.commands == []
        assert (await c.post("/hub/tasmota/t1/pinfinder/nope", headers=admin, json={})).status_code == 404
        idle = (await c.get("/hub/tasmota/t1/pinfinder", headers=admin)).json()
        assert idle["status"] == "idle" and idle["kinds"] == ["bulb", "strip", "dimmer"]
        refused = await c.post("/hub/tasmota/t1/pinfinder/start", headers=admin, json={})
        assert refused.status_code == 400 and dev.commands == []
        out = (await c.post("/hub/tasmota/t1/pinfinder/start", headers=admin, json={"confirm": True})).json()
        assert out["question"]["id"] == "baseline"
        stale = await c.post("/hub/tasmota/t1/pinfinder/answer", headers=admin, json={"step": 0, "answer": "dark"})
        assert stale.status_code == 409
        nxt = (await c.post("/hub/tasmota/t1/pinfinder/answer", headers=admin,
                            json={"step": out["question"]["step"], "answer": "dark"})).json()
        assert nxt["question"]["id"] == "single" and nxt["question"]["pin"] == 4
        done = (await c.post("/hub/tasmota/t1/pinfinder/cancel", headers=admin, json={"confirm": True})).json()
        assert done["restored"] and dev.template == GENERIC
        saved = (await c.post("/hub/tasmota/measured", headers=admin, json=_seed(runtime))).json()
        rec_id = saved["template"]["id"]
        cat = (await c.get("/hub/tasmota/templates?kind=bulb&q=lotton", headers=admin)).json()
        assert cat["templates"] == [] and [m["id"] for m in cat["measured"]] == [rec_id]
        assert (await c.delete(f"/hub/tasmota/measured/{rec_id}", headers=admin)).json()["status"] == "deleted"
        assert (await c.delete(f"/hub/tasmota/measured/{rec_id}", headers=admin)).status_code == 404
