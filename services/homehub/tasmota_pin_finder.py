"""The pin finder: work out a Tasmota light's template by lighting one pin at a time and asking what it looks like.

A bulb reflashed to Tasmota whose template is in no catalog still has its LEDs on a few GPIO pins. This does
what a person does by hand, one step at a time over the API, with the person answering each question:

1. **Save.** Read the device's template, module and the two options the probe changes (``SetOption68``,
   ``SetOption92``) and keep them in ``entry.data["pin_finder_backup"]`` before anything is sent, so they can
   be put back after a cancel, a failure, an abandoned run (``IDLE_RESTORE_S``) or a hub restart.
2. **Probe.** Load a template with up to five candidate pins as PWM1..PWM5 and ``SetOption68 1`` (every PWM
   its own channel), wait for the restart, and check the device runs it. The first pins are the ones bulbs use
   (``COMMON``); "try the rest" loads the others (``REST``), keeping any pin already found.
3. **Ask.** With every channel off: is it dark? Then each pin alone at full: dark, cool white, warm white,
   white, red, green, blue, or another colour?
4. **Narrow down.** Red, green and blue pins make RGB (one more white makes RGBW, a cool and a warm RGBCW). Two
   whites are cold + warm (if the answers did not say which is cooler, it shows one then the other and asks).
   One white is either a dimmable white or the brightness pin of a brightness + colour-temperature bulb: it
   shows that pin alone, then adds each dark pin (Tasmota's ``Delay``), and asks whether the white got cooler
   or warmer. The pin that changes it is the CT pin. Tasmota's CT mode (``SetOption92 1``) drives the CT pin
   harder the warmer the setting, so a pin that makes the light *cooler* when driven is inverted (PWMi2).
5. **Write and check.** Load the template (``SetOption68 0``, ``SetOption92`` as the layout needs), then check
   by eye: CT 153 cool? CT 500 warm? (each colour for RGB). A swapped answer is fixed once (the CT pin's
   inversion, the cold/warm pins, or the colour order) and checked again; anything else puts the saved
   template back.

Only lights in this version. A plug or switch would mean driving pins that may be inputs (a button wired to
ground, a power meter's pulse output), and finding a button needs the device to read pins, not drive them.

Every command goes through ``TasmotaDriver.cm`` (the web password stays in the driver; nothing here logs it).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from services.homehub import tasmota_templates as tt
from services.homehub.core import AuthRequired, DeviceUnavailable

log = logging.getLogger("homehub.tasmota_pin_finder")

PROBE_NAME = "HomeHub pin finder"
#: Tasmota runs at most five PWM channels on an ESP8266, so a probe holds five pins.
MAX_PROBE = 5
#: The pins bulbs use, tried first, and the rest ("try the rest"), per chip family. Never the flash pins, never
#: input-only pins (ESP32 34-39), never an ESP32-S3's USB or PSRAM pins.
COMMON = {
    "esp8266": [4, 5, 12, 13, 14],
    "esp32": [4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33],
    "esp32c3": [3, 4, 5, 6, 7, 10, 0, 1],
    "esp32s3": [4, 5, 6, 7, 15, 16, 17, 18, 8, 3, 9, 10, 11, 12, 13, 14],
}
REST = {
    "esp8266": [0, 2, 15, 16, 1, 3],
    "esp32": [0, 2, 1, 3],
    "esp32c3": [2, 8, 9, 18, 19, 20, 21],
    "esp32s3": [1, 2, 21, 38, 39, 40, 41, 42, 47, 48, 0],
}
#: An ESP8285 has no external flash on GPIO9 and 10, so they are pins like the others.
ESP8285_REST = [9, 10]
LIGHT_KINDS = ("bulb", "strip", "dimmer")
#: Tasmota's ``Delay`` is in tenths of a second: how long the first half of a comparison is shown.
SHOW_TENTHS = 20
#: A run nobody has answered for this long is ended and the saved template put back.
IDLE_RESTORE_S = 900.0

PWM, PWM_I = 416, 448

SINGLE_ANSWERS = [("dark", "Dark"), ("cool", "Cool white"), ("warm", "Warm white"), ("white", "White"),
                  ("red", "Red"), ("green", "Green"), ("blue", "Blue"), ("other", "Another colour")]
WHITES = ("cool", "warm", "white")
COLOURS = ("red", "green", "blue")

_sessions: dict[str, Session] = {}
_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task[Any]] = set()


class FinderError(tt.TemplateError):
    pass


@dataclass
class Session:
    entry_id: str
    family: str
    kind: str
    manufacturer: str
    model: str
    title: str
    pending: list[int]
    loaded: list[int] = field(default_factory=list)
    looks: dict[int, str] = field(default_factory=dict)
    paired: dict[int, str] = field(default_factory=dict)
    phase: str = "probe"
    step: int = 0
    question: dict[str, Any] | None = None
    show: str | None = None
    layout: dict[str, Any] | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    check_answers: list[str] = field(default_factory=list)
    fixed: bool = False
    rest_offered: bool = False
    tried_rest: bool = False
    status: str = "asking"
    message: str | None = None
    result: dict[str, Any] | None = None
    touched: float = field(default_factory=time.monotonic)
    idle: Any = None
    final: tuple[Any, Any] | None = None

    def public(self) -> dict[str, Any]:
        found = {str(p): look for p, look in self.looks.items() if look != "dark"}
        return {"active": self.status == "asking", "status": self.status, "phase": self.phase,
                "question": self.question, "message": self.message, "result": self.result,
                "manufacturer": self.manufacturer, "model": self.model, "kind": self.kind,
                "pins_tried": sorted(self.looks), "lit": found, "can_try_rest": bool(self.pending)}


# -- what to ask (pure) --------------------------------------------------------------------------------------

def candidate_pins(family: str, chip: str | None) -> tuple[list[int], list[int]]:
    rest = list(REST[family])
    if family == "esp8266" and "8285" in str(chip or ""):
        rest += ESP8285_REST
    return list(COMMON[family]), rest


def probe_template(family: str, pins: list[int]) -> dict[str, Any]:
    """PWM1..PWMn on ``pins`` (in that order), nothing else."""
    numbers = tt.gpio_numbers(family)
    gpio = [0] * len(numbers)
    for ch, pin in enumerate(pins):
        gpio[numbers.index(pin)] = PWM + ch
    return {"NAME": PROBE_NAME, "GPIO": gpio, "FLAG": 0, "BASE": 18}


def classify_whites(looks: dict[int, str], pins: list[int]) -> tuple[int, int] | None:
    """``(cold pin, warm pin)`` of two white pins when the answers say which is which, else None."""
    a, b = pins
    la, lb = looks[a], looks[b]
    if la == lb:
        return None
    if la == "cool" or lb == "warm":
        return a, b
    if la == "warm" or lb == "cool":
        return b, a
    return None


def layout_template(layout: dict[str, Any], family: str, name: str) -> tuple[dict[str, Any], list[str]]:
    """The final template and the commands it needs, from a layout (``type`` and the role of each pin)."""
    numbers = tt.gpio_numbers(family)
    gpio = [0] * len(numbers)
    pins = layout["pins"]
    kind = layout["type"]
    order = {"dimmer": ["bright"], "cw": ["cold", "warm"], "ct": ["bright", "ct"], "rgb": ["red", "green", "blue"],
             "rgbw": ["red", "green", "blue", "white"], "rgbcw": ["red", "green", "blue", "cold", "warm"]}[kind]
    for ch, role in enumerate(order):
        inverted = kind == "ct" and role == "ct" and layout.get("invert")
        gpio[numbers.index(pins[role])] = (PWM_I if inverted else PWM) + ch
    commands = ["SO92 1"] if kind == "ct" else []
    tpl: dict[str, Any] = {"NAME": name, "GPIO": gpio, "FLAG": 0, "BASE": 18}
    if commands:
        tpl["CMND"] = " | ".join(commands)
    return tpl, commands


def checks_for(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """What to show and what the person should see, to check the written template by eye."""
    kind = layout["type"]
    if kind == "dimmer":
        return [{"cmd": "Backlog Power ON; Dimmer 100", "expect": "on", "text": "Is it lit now?",
                 "answers": [("on", "Yes, it is lit"), ("off", "No, it is dark")]},
                {"cmd": "Power OFF", "expect": "off", "text": "Is it dark now?",
                 "answers": [("off", "Yes, it is dark"), ("on", "No, it is still lit")]}]
    out = []
    if kind in ("rgb", "rgbw", "rgbcw"):
        for hue, colour in ((0, "red"), (120, "green"), (240, "blue")):
            out.append({"cmd": f"Backlog Power ON; Dimmer 100; HSBColor {hue},100,100", "expect": colour,
                        "text": "What colour is it now?",
                        "answers": [("red", "Red"), ("green", "Green"), ("blue", "Blue"), ("white", "White"),
                                    ("other", "Something else")]})
    if kind == "rgbw":
        out.append({"cmd": "Backlog Power ON; White 100", "expect": "white", "text": "Is it white now?",
                    "answers": [("white", "Yes, white"), ("other", "No")]})
    if kind in ("cw", "ct", "rgbcw"):
        whites = [("cool", "Cool white"), ("warm", "Warm white"), ("other", "Something else")]
        out.append({"cmd": "Backlog Power ON; Dimmer 100; CT 153", "expect": "cool",
                    "text": "Is it a cool, bluish white now?", "answers": whites})
        out.append({"cmd": "Backlog Power ON; Dimmer 100; CT 500", "expect": "warm",
                    "text": "And now, is it a warm, yellowish white?", "answers": whites})
    return out


def how_text(layout: dict[str, Any]) -> str:
    pins = layout["pins"]
    kind = layout["type"]
    if kind == "ct":
        return (f"Found by the pin finder: GPIO{pins['bright']} alone lit it (brightness); adding GPIO{pins['ct']} "
                f"made it {'cooler' if layout.get('invert') else 'warmer'}, so GPIO{pins['ct']} is colour temperature"
                f"{' (inverted)' if layout.get('invert') else ''}. Checked by eye: CT 153 cool, CT 500 warm.")
    if kind == "cw":
        return (f"Found by the pin finder: GPIO{pins['cold']} alone lit it cool white, GPIO{pins['warm']} warm "
                "white. Checked by eye: CT 153 cool, CT 500 warm.")
    if kind == "dimmer":
        return f"Found by the pin finder: GPIO{pins['bright']} alone lit it and no other pin changed it."
    named = ", ".join(f"{role} GPIO{pin}" for role, pin in pins.items())
    return f"Found by the pin finder, one pin at a time ({named}), and checked by eye."


# -- talking to the device -------------------------------------------------------------------------------------

async def _option(drv: Any, number: int) -> int:
    reply = await tt._cm(drv, f"SetOption{number}")
    value = reply.get(f"SetOption{number}")
    if isinstance(value, str):
        return 1 if value.strip().upper() in ("ON", "1") else 0
    return 1 if value in (1, True) else 0


def _channels_off(count: int) -> list[str]:
    return [f"Channel{ch} 0" for ch in range(1, count + 1)]


def _single(s: Session, pin: int) -> str:
    ch = s.loaded.index(pin) + 1
    return "Backlog " + "; ".join([*_channels_off(len(s.loaded)), f"Channel{ch} 100", f"Power{ch} ON"])


def _pair(s: Session, first: int, second: int, *, swap: bool) -> str:
    """Show ``first`` alone, then add ``second`` (``swap``: show ``second`` instead of adding it)."""
    a, b = s.loaded.index(first) + 1, s.loaded.index(second) + 1
    then = [f"Power{a} OFF", f"Channel{a} 0"] if swap else []
    return "Backlog " + "; ".join([*_channels_off(len(s.loaded)), f"Channel{a} 100", f"Power{a} ON",
                                   f"Delay {SHOW_TENTHS}", *then, f"Channel{b} 100", f"Power{b} ON"])


async def _restart_into(runtime: Any, entry: Any, drv: Any, tpl: dict[str, Any] | None, module: int,
                        options: list[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Set ``options``, load ``tpl`` with ``module`` and wait for the restart: ``(Status 0, facts)``."""
    before = await tt._read(runtime, entry, drv)
    restarted, status, after, _sent = await tt.send_and_restart(runtime, entry, drv, tpl, module, options,
                                                                before.get("uptime_s"))
    if not restarted and status is not None:
        # The options need a restart to take effect even when the template was already the device's.
        await tt._cm(drv, "Restart 1")
        _restarted, status = await tt._wait_for_restart(drv, (status.get("StatusSTS") or {}).get("UptimeSec"))
        after = await tt._read(runtime, entry, drv) if status is not None else None
    return status, after


def _backup(entry: Any) -> dict[str, Any] | None:
    b = entry.data.get("pin_finder_backup")
    return b if isinstance(b, dict) else None


def _set_data(runtime: Any, entry: Any, **changes: Any) -> None:
    data = dict(entry.data)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    entry.data = data
    runtime.store.save_config_entry(entry)


async def _restore(runtime: Any, entry: Any) -> tuple[bool, str]:
    """Put back the template, module and options saved before the run: ``(done, what happened)``."""
    backup = _backup(entry)
    if backup is None:
        return True, "There was nothing to put back."
    tpl = tt.validate_template(backup["template"]) if backup.get("template") else None
    module = int(backup.get("module") if isinstance(backup.get("module"), int) else 0)
    options = [f"SetOption68 {int(backup.get('so68') or 0)}", f"SetOption92 {int(backup.get('so92') or 0)}"]
    drv, made = tt._driver_for(runtime, entry)
    try:
        status, after = await _restart_into(runtime, entry, drv, tpl, module, options)
    except tt.TemplateError as exc:
        return False, f"It could not be put back ({exc}). The saved template is kept: try Restore again."
    finally:
        if made:
            await drv.async_unload()
    ok = after is not None and after.get("module") == module and (tpl is None or tt.same_template(after.get("template"), tpl))
    if not ok:
        return False, ("The saved template was sent, but the device did not come back with it. It is kept: "
                       "try Restore again.")
    _set_data(runtime, entry, pin_finder_backup=None)
    try:
        await tt.reload_entry(runtime, entry, status, after)
    except tt.TemplateError:
        log.warning("tasmota %s: restored, but reloading the entry failed", entry.entry_id, exc_info=True)
    log.info("tasmota %s (%s): pin finder put the saved template back", entry.title, entry.entry_id)
    name = (tpl or {}).get("NAME")
    return True, f"Put back as it was{f' ({name})' if name else ''}."


# -- the run ---------------------------------------------------------------------------------------------------

def _question(s: Session, qid: str, text: str, answers: list[tuple[str, str]], *, hint: str | None = None,
              again: bool = True, pin: int | None = None) -> None:
    s.step += 1
    total = len(s.loaded)
    done = len([p for p in s.loaded if p in s.looks])
    s.question = {"step": s.step, "id": qid, "text": text, "hint": hint, "pin": pin,
                  "answers": [{"value": v, "label": label} for v, label in answers], "again": again,
                  "progress": {"done": done, "total": total}}


async def _load_probe(runtime: Any, entry: Any, drv: Any, s: Session) -> None:
    keep = [p for p in s.loaded if s.looks.get(p, "dark") != "dark"]
    room = MAX_PROBE - len(keep)
    s.loaded = keep + s.pending[:room]
    s.pending = s.pending[room:]
    probe = probe_template(s.family, s.loaded)
    _status, after = await _restart_into(runtime, entry, drv, probe, 0, ["SetOption92 0", "SetOption68 1"])
    if after is None or not tt.same_template(after.get("template"), probe) or after.get("module") != 0:
        raise FinderError("The device did not come back running the test template.", 502)
    log.info("tasmota %s (%s): pin finder probing GPIO %s", entry.title, entry.entry_id, s.loaded)
    await tt._cm(drv, "Backlog " + "; ".join(_channels_off(len(s.loaded))))
    s.phase, s.show = "baseline", "Backlog " + "; ".join(_channels_off(len(s.loaded)))
    _question(s, "baseline", f"Every pin being tested is off. Is {s.title} dark?",
              [("dark", "Yes, it is dark"), ("lit", "No, it is lit")],
              hint="Look at the light itself, not a power or Wi-Fi LED.")


async def _next(runtime: Any, entry: Any, drv: Any, s: Session) -> None:
    """Send the next probe and set the next question, or move on to writing the template."""
    todo = [p for p in s.loaded if p not in s.looks]
    if todo:
        pin = todo[0]
        s.phase, s.show = "single", _single(s, pin)
        await tt._cm(drv, s.show)
        _question(s, "single", f"Only GPIO{pin} is on now. What does {s.title} look like?", SINGLE_ANSWERS,
                  hint="Dark is a fine answer: most pins drive nothing.", pin=pin)
        return
    lit = {p: look for p, look in s.looks.items() if look != "dark"}
    if any(look == "other" for look in lit.values()):
        raise FinderError("A pin lit it in a colour that is not red, green or blue, which the pin finder cannot "
                          "map. The light may need a template from its maker.", 422)
    colours: dict[str, int] = {}
    for p, look in lit.items():
        if look in COLOURS:
            if look in colours:
                raise FinderError(f"Two pins both lit it {look}, so the colours cannot be told apart.", 422)
            colours[look] = p
    whites = [p for p, look in lit.items() if look in WHITES]
    if not lit or (colours and len(colours) < 3):
        await _offer_rest(s, "No pin lit it." if not lit else
                          f"Only {' and '.join(sorted(colours))} were found, not all three colours.")
        return
    if len(whites) > 2:
        raise FinderError("More than two pins lit it white, which the pin finder cannot map.", 422)
    if colours:
        pins = {c: colours[c] for c in COLOURS}
        if not whites:
            await _write(runtime, entry, drv, s, {"type": "rgb", "pins": pins})
        elif len(whites) == 1:
            await _write(runtime, entry, drv, s, {"type": "rgbw", "pins": {**pins, "white": whites[0]}})
        else:
            await _two_whites(runtime, entry, drv, s, whites, {"type": "rgbcw", "pins": pins})
        return
    if len(whites) == 2:
        await _two_whites(runtime, entry, drv, s, whites, {"type": "cw", "pins": {}})
        return
    bright = whites[0]
    dark = [p for p in s.loaded if s.looks.get(p) == "dark" and p not in s.paired]
    if dark:
        other = dark[0]
        s.phase, s.show = "pair", _pair(s, bright, other, swap=False)
        await tt._cm(drv, s.show)
        _question(s, "pair", f"It showed GPIO{bright} alone for two seconds, then turned GPIO{other} on as well. "
                  "Did the white change when the second pin came on?",
                  [("cooler", "It got cooler (bluer)"), ("warmer", "It got warmer (yellower)"),
                   ("same", "No change"), ("dark", "It went dark")], pin=other,
                  hint="Use Show again to watch it once more.")
        return
    if s.pending and not s.rest_offered:
        await _offer_rest(s, f"GPIO{bright} lights it, and no other pin tried so far changes its white.",
                          dimmer=True)
        return
    await _write(runtime, entry, drv, s, {"type": "dimmer", "pins": {"bright": bright}})


async def _offer_rest(s: Session, why: str, *, dimmer: bool = False) -> None:
    s.rest_offered = True
    if not s.pending:
        raise FinderError(why + " No pin the pin finder can test lights it. Many bulbs drive their LEDs through a "
                          "chip (SM2135, BP5758D, SM2235) rather than PWM pins: search the catalog for the model.",
                          422)
    s.phase, s.show = "offer_rest", None
    answers = [("rest", f"Try more pins ({len(s.pending)} left)"), ("stop", "Stop and put it back")]
    if dimmer:
        answers.insert(1, ("dimmer", "Use it as a dimmable white light"))
    _question(s, "offer_rest", why + " Try pins lights use less often? They can be wired to something else, "
              "so only do this on a light.", answers, again=False)


async def _two_whites(runtime: Any, entry: Any, drv: Any, s: Session, whites: list[int],
                      layout: dict[str, Any]) -> None:
    pair = classify_whites(s.looks, whites)
    if pair is not None:
        await _write(runtime, entry, drv, s, {**layout, "pins": {**layout["pins"], "cold": pair[0], "warm": pair[1]}})
        return
    a, b = whites
    s.layout = layout
    s.phase, s.show = "compare", _pair(s, a, b, swap=True)
    await tt._cm(drv, s.show)
    _question(s, "compare", f"It showed one white (GPIO{a}) for two seconds, then another (GPIO{b}). "
              "Which was cooler (bluer)?",
              [("first", "The first"), ("second", "The second"), ("same", "They looked the same")])


async def _write(runtime: Any, entry: Any, drv: Any, s: Session, layout: dict[str, Any]) -> None:
    """Load the final template, then start the checks."""
    s.layout = layout
    name = " ".join(f"{s.manufacturer} {s.model}".split())[:30].strip() or s.title[:30] or "Light"
    tpl, commands = layout_template(layout, s.family, name.replace(";", " "))
    so92 = 1 if layout["type"] == "ct" else 0
    status, after = await _restart_into(runtime, entry, drv, tpl, 0, ["SetOption68 0", f"SetOption92 {so92}"])
    if after is None or not tt.same_template(after.get("template"), tpl) or after.get("module") != 0:
        raise FinderError("The device did not come back running the template that was found.", 502)
    log.info("tasmota %s (%s): pin finder wrote a %s template, checking it by eye", entry.title, entry.entry_id,
             layout["type"])
    s.final = (status, after)
    s.result = {"template": tt.template_text(tpl), "commands": commands, "type": layout["type"],
                "pins": dict(layout["pins"]), "invert": bool(layout.get("invert"))}
    s.checks = checks_for(layout)
    s.check_answers = []
    await _ask_check(drv, s)


async def _ask_check(drv: Any, s: Session) -> None:
    check = s.checks[len(s.check_answers)]
    s.phase, s.show = "verify", check["cmd"]
    await tt._cm(drv, s.show)
    _question(s, "verify", check["text"], check["answers"],
              hint=f"Checking the template that was found ({len(s.check_answers) + 1} of {len(s.checks)}).")


def _fix(layout: dict[str, Any], checks: list[dict[str, Any]], answers: list[str]) -> dict[str, Any] | None:
    """The layout with the one mistake the answers point to put right, or None when they point to none."""
    kind = layout["type"]
    pins = dict(layout["pins"])
    seen = {c["expect"]: a for c, a in zip(checks, answers, strict=False)}
    if kind == "ct" and seen.get("cool") == "warm":
        return {**layout, "invert": not layout.get("invert")}
    if kind in ("cw", "rgbcw") and seen.get("cool") == "warm":
        pins["cold"], pins["warm"] = pins["warm"], pins["cold"]
        return {**layout, "pins": pins}
    if kind in ("rgb", "rgbw", "rgbcw") and all(c in seen for c in COLOURS):
        shown = [seen[c] for c in COLOURS]
        if sorted(shown) == sorted(COLOURS) and shown != list(COLOURS):
            # The channel meant for red lit what the person called shown[0], and so on.
            return {**layout, "pins": {**pins, **{shown[i]: layout["pins"][COLOURS[i]] for i in range(3)}}}
    return None


async def _finish(runtime: Any, entry: Any, s: Session) -> None:
    backup = _backup(entry) or {}
    result = dict(s.result or {})
    result.update({"kind": s.kind, "how": how_text(s.layout or {}), "measured_at": time.strftime("%Y-%m-%d")})
    changes: dict[str, Any] = {"pin_finder_backup": None, "pin_finder_result": result}
    if backup.get("template"):
        changes.update(previous_template=backup["template"], previous_module=backup.get("module"))
    _set_data(runtime, entry, **changes)
    s.status, s.phase, s.question, s.result = "done", "done", None, result
    s.message = "Found and checked. The device now runs this template."
    log.info("tasmota %s (%s): pin finder done (%s)", entry.title, entry.entry_id, result["type"])


# -- the API ---------------------------------------------------------------------------------------------------

def _lock(entry_id: str) -> asyncio.Lock:
    lock = _locks.setdefault(entry_id, asyncio.Lock())
    if lock.locked():
        raise FinderError("The pin finder is busy with this device; wait for its answer.", 409)
    return lock


def _touch(runtime: Any, s: Session) -> None:
    s.touched = time.monotonic()
    if s.idle is not None:
        s.idle.cancel()
    if s.status != "asking":
        return
    step = s.step
    loop = asyncio.get_running_loop()
    def fire() -> None:
        task = loop.create_task(_idle_restore(runtime, s.entry_id, step))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    s.idle = loop.call_later(IDLE_RESTORE_S, fire)


async def _idle_restore(runtime: Any, entry_id: str, step: int) -> None:
    s = _sessions.get(entry_id)
    if s is None or s.step != step or s.status != "asking":
        return
    log.info("tasmota %s: pin finder left unanswered, putting the saved template back", entry_id)
    try:
        await cancel(runtime, entry_id, {"confirm": True}, reason="Nobody answered for a while, so it was stopped.")
    except Exception:
        log.warning("tasmota %s: putting the saved template back after an abandoned run failed", entry_id,
                    exc_info=True)


def state(runtime: Any, entry_id: str) -> dict[str, Any]:
    entry = tt._tasmota_entry(runtime, entry_id)
    s = _sessions.get(entry_id)
    backup = _backup(entry)
    out: dict[str, Any] = {"active": False, "status": "idle", "question": None, "result": None, "message": None,
                           "kinds": list(LIGHT_KINDS), "interrupted": False}
    if s is not None:
        out.update(s.public())
    if backup is not None and (s is None or s.status not in ("asking",)):
        out.update(interrupted=True, backup_template=backup.get("template"),
                   message=(out.get("message") or "") + (" " if out.get("message") else "")
                   + "A pin finder run did not finish. Its saved template can be put back.")
    return out


async def start(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"confirm": true, "manufacturer"?, "model"?, "kind"?}``: save the device's template, load the probe and
    ask the first question."""
    entry = tt._tasmota_entry(runtime, entry_id)
    if body.get("confirm") is not True:
        raise FinderError("Nothing was sent. The pin finder restarts the device and changes its template while it "
                          "runs; send \"confirm\": true to go ahead.")
    kind = body.get("kind") or "bulb"
    if kind not in LIGHT_KINDS:
        raise FinderError("The pin finder works on lights: kind is one of " + ", ".join(LIGHT_KINDS) + ".")
    maker = " ".join(str(body.get("manufacturer") or "").split())[:60]
    model = " ".join(str(body.get("model") or "").split())[:60]
    if (s := _sessions.get(entry_id)) is not None and s.status == "asking":
        raise FinderError("The pin finder is already running on this device.", 409)
    if _backup(entry) is not None:
        raise FinderError("A pin finder run on this device did not finish. Put its saved template back first.", 409)
    if entry_id in tt.BUSY:
        raise FinderError(tt.BUSY[entry_id], 409)
    template_lock = tt._locks.setdefault(entry_id, asyncio.Lock())
    if template_lock.locked():
        raise FinderError("A template is being loaded onto this device; wait for it.", 409)
    async with _lock(entry_id):
        drv, made = tt._driver_for(runtime, entry)
        try:
            facts = await tt._read(runtime, entry, drv)
            family = tt.chip_family(facts.get("chip"))
            if family is None:
                raise FinderError(f"The device's chip ({facts.get('chip') or 'unknown'}) is not one the pin finder "
                                  "knows the pins of.", 409)
            backup = {"template": tt.template_text(facts["template"]) if facts.get("template") else None,
                      "module": facts.get("module"), "so68": await _option(drv, 68), "so92": await _option(drv, 92),
                      "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            # Saved before anything is sent: whatever happens next, this is what goes back.
            _set_data(runtime, entry, pin_finder_backup=backup, pin_finder_result=None)
            common, rest = candidate_pins(family, facts.get("chip"))
            s = Session(entry_id=entry_id, family=family, kind=kind, manufacturer=maker, model=model,
                        title=_device_name(runtime, entry), pending=common + rest)
            _sessions[entry_id] = s
            tt.BUSY[entry_id] = "The pin finder is running on this device; finish or cancel it first."
            await _load_probe(runtime, entry, drv, s)
        except (tt.TemplateError, AuthRequired, DeviceUnavailable) as exc:
            if _backup(entry) is None:
                raise
            return await _fail(runtime, entry, str(exc))
        finally:
            if made:
                await drv.async_unload()
        _touch(runtime, s)
        log.info("tasmota %s (%s): pin finder started (%s)", entry.title, entry.entry_id, family)
        return s.public()


def _device_name(runtime: Any, entry: Any) -> str:
    from services.homehub.tasmota_measured import device_name_of

    return device_name_of(runtime, entry)


async def answer(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"step": <the question's step>, "answer": <one of its values> or "again"}``."""
    entry = tt._tasmota_entry(runtime, entry_id)
    s = _sessions.get(entry_id)
    if s is None or s.status != "asking" or s.question is None:
        raise FinderError("The pin finder is not running on this device.", 409)
    if body.get("step") != s.question["step"]:
        raise FinderError("That answer was for an earlier question.", 409)
    value = body.get("answer")
    allowed = {a["value"] for a in s.question["answers"]}
    if value == "again" and s.question.get("again") and s.show:
        async with _lock(entry_id):
            drv, made = tt._driver_for(runtime, entry)
            try:
                await tt._cm(drv, s.show)
            except tt.TemplateError as exc:
                return await _fail(runtime, entry, str(exc))
            finally:
                if made:
                    await drv.async_unload()
        _touch(runtime, s)
        return s.public()
    if value not in allowed:
        raise FinderError("Answer with one of: " + ", ".join(sorted(allowed)) + ".")
    async with _lock(entry_id):
        drv, made = tt._driver_for(runtime, entry)
        try:
            await _apply_answer(runtime, entry, drv, s, str(value))
        except (tt.TemplateError, AuthRequired, DeviceUnavailable) as exc:
            return await _fail(runtime, entry, str(exc))
        finally:
            if made:
                await drv.async_unload()
    if s.status == "done":
        tt.BUSY.pop(entry_id, None)
        status, after = s.final or (None, None)
        try:
            added, _stale = await tt.reload_entry(runtime, entry, status, after)
        except tt.TemplateError:
            log.warning("tasmota %s: the template was found, but reloading the entry failed", entry_id, exc_info=True)
            added = []
        if added:
            s.message = (s.message or "") + f" Added {len(added)} entit{'y' if len(added) == 1 else 'ies'}."
    _touch(runtime, s)
    return s.public()


async def _apply_answer(runtime: Any, entry: Any, drv: Any, s: Session, value: str) -> None:
    q = s.question or {}
    qid = q.get("id")
    if qid == "baseline":
        if value == "lit":
            raise FinderError("With every pin it tests turned off, the light is still on. One of these pins may be "
                              "wired the other way round (on when off), which the pin finder cannot map yet.", 422)
        await _next(runtime, entry, drv, s)
    elif qid == "single":
        s.looks[int(q["pin"])] = value
        await _next(runtime, entry, drv, s)
    elif qid == "pair":
        other = int(q["pin"])
        s.paired[other] = value
        bright = next(p for p, look in s.looks.items() if look in WHITES)
        if value in ("cooler", "warmer"):
            # Tasmota's CT mode drives the CT pin harder the warmer it is set: a pin that cools it is inverted.
            await _write(runtime, entry, drv, s, {"type": "ct", "pins": {"bright": bright, "ct": other},
                                                  "invert": value == "cooler"})
        elif value == "dark":
            raise FinderError(f"Turning GPIO{other} on turned the light off, which the pin finder cannot map.", 422)
        else:
            await _next(runtime, entry, drv, s)
    elif qid == "compare":
        if value == "same":
            raise FinderError("Two pins light it white and they look the same, so cold and warm cannot be told "
                              "apart.", 422)
        whites = [p for p, look in s.looks.items() if look in WHITES]
        cold, warm = (whites[0], whites[1]) if value == "first" else (whites[1], whites[0])
        layout = s.layout or {"type": "cw", "pins": {}}
        await _write(runtime, entry, drv, s, {**layout, "pins": {**layout["pins"], "cold": cold, "warm": warm}})
    elif qid == "offer_rest":
        if value == "stop":
            raise FinderError("Stopped.", 200)
        if value == "dimmer":
            bright = next(p for p, look in s.looks.items() if look in WHITES)
            await _write(runtime, entry, drv, s, {"type": "dimmer", "pins": {"bright": bright}})
            return
        s.tried_rest = True
        s.rest_offered = False
        await _load_probe(runtime, entry, drv, s)
    elif qid == "verify":
        s.check_answers.append(value)
        n = len(s.check_answers)
        colour_checks = [i for i, c in enumerate(s.checks) if c["expect"] in COLOURS]
        if colour_checks and n - 1 < colour_checks[-1] and value in COLOURS:
            # Every colour is seen before deciding whether, and how, they are swapped.
            await _ask_check(drv, s)
            return
        wrong = [i for i in range(n) if s.check_answers[i] != s.checks[i]["expect"]]
        if not wrong:
            if n == len(s.checks):
                await _finish(runtime, entry, s)
            else:
                await _ask_check(drv, s)
            return
        fixed = None if s.fixed else _fix(s.layout or {}, s.checks, s.check_answers)
        if fixed is None:
            check = s.checks[wrong[0]]
            raise FinderError(f"It was meant to look {check['expect']} but you saw {s.check_answers[wrong[0]]}, "
                              "so the template that was found is not right.", 422)
        s.fixed = True
        log.info("tasmota %s: pin finder correcting the %s template after the check", s.entry_id, fixed["type"])
        await _write(runtime, entry, drv, s, fixed)


async def _fail(runtime: Any, entry: Any, why: str, *, started: bool = True) -> dict[str, Any]:
    """End the run and put the saved template back; the answer says both what went wrong and what was done."""
    s = _sessions.get(entry.entry_id)
    restored, how = (await _restore(runtime, entry)) if started else (True, "")
    tt.BUSY.pop(entry.entry_id, None)
    stopped = why == "Stopped."
    message = why if stopped else f"The pin finder stopped: {why}"
    message = f"{message} {how}".strip()
    if s is not None:
        if s.idle is not None:
            s.idle.cancel()
        s.status, s.phase, s.question, s.message = ("cancelled" if stopped else "failed"), "ended", None, message
    log.info("tasmota %s (%s): pin finder ended (%s), restored=%s", entry.title, entry.entry_id,
             "stopped" if stopped else "failed", restored)
    out = s.public() if s is not None else {"active": False, "status": "failed", "question": None, "result": None}
    out.update(message=message, restored=restored, interrupted=not restored)
    return out


async def cancel(runtime: Any, entry_id: str, body: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    """``{"confirm": true}``: stop the run (or a run a hub restart interrupted) and put the saved template back."""
    entry = tt._tasmota_entry(runtime, entry_id)
    if body.get("confirm") is not True:
        raise FinderError("Nothing was sent. Putting the saved template back restarts the device; send "
                          "\"confirm\": true to go ahead.")
    s = _sessions.get(entry_id)
    if _backup(entry) is None and (s is None or s.status != "asking"):
        raise FinderError("The pin finder is not running on this device.", 409)
    async with _lock(entry_id):
        if s is not None and s.idle is not None:
            s.idle.cancel()
        restored, how = await _restore(runtime, entry)
    tt.BUSY.pop(entry_id, None)
    message = f"{reason + ' ' if reason else 'Cancelled. '}{how}"
    if s is not None:
        s.status, s.phase, s.question, s.message = "cancelled", "ended", None, message
    out = s.public() if s is not None else {"active": False, "status": "cancelled", "question": None,
                                            "result": None}
    out.update(message=message, restored=restored, interrupted=not restored)
    return out
