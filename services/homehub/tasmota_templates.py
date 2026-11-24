"""Tasmota device templates: a catalog, reading what a device has, suggesting one, and loading it.

A Tasmota template is the device's GPIO map (``{"NAME":...,"GPIO":[...],"FLAG":...,"BASE":...}``). A bulb
reflashed to Tasmota cannot drive its LEDs until it has the right one: a GPIO not mapped to its PWM channel
does nothing, and a wrong map can drive a pin that should not be driven. So nothing here is written to a
device unless an admin asked for it with ``confirm: true``.

- The catalog (``data/tasmota_templates.json``) holds widely used devices, each template copied character
  for character from its page on templates.blakadder.com (``source_url``). Never add one from memory.
- ``read_device`` asks the device for ``Template``, ``Module`` and ``Status 0``.
- ``suggest`` ranks catalog entries with a small, explainable score: words in the device's names that
  name a maker or a model, the kind of device its names say it is, what it drives now (a light, relays,
  an energy meter), and its chip. A template for another chip family is never offered. Templates measured
  in this household (``tasmota_measured.py``) are scored the same way and rank first when the device's names
  match their maker or model.
- When the device says nothing that identifies it (a Generic module that drives nothing, and names that match
  no maker or model), ``read_device`` says so and offers the pin finder (``tasmota_pin_finder.py``) instead of
  ranking a template that only matches a word like "lamp"; those are returned apart, as ``guesses``.
- ``apply_template`` sends ``Backlog Template <json>; Module 0`` (an entry's extra commands first, each on
  its own), waits for the restart, reads ``Template`` back to verify it, keeps the template it replaced in
  ``entry.data["previous_template"]`` (and ``previous_module``) and reloads the entry, adding the light or
  relay entities the new template brought. ``restore_previous`` sends the kept one back.

Every ``/cm`` request goes through the driver's own transport (``TasmotaDriver.cm``), so the web
password and the device's answers are handled in one place.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any

from services.homehub.core import AuthRequired, DeviceUnavailable, HubError

log = logging.getLogger("homehub.tasmota_templates")

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "tasmota_templates.json")
KINDS = ("bulb", "plug", "switch", "dimmer", "strip", "relay")
#: GPIO list lengths per chip family: an ESP8266/ESP8285 has 14 template pins, an ESP32 36,
#: an ESP32-C3 22, an ESP32-S3 49. Tasmota's old (before 9.1) format had 13 and other numbers.
GPIO_LENGTHS = {"esp8266": (14,), "esp32": (36,), "esp32c3": (22,), "esp32s3": (49,)}
#: Tasmota reads a command of up to about 800 characters; the template is one part of a Backlog.
MAX_TEMPLATE_CHARS = 700

#: Seconds before the first look after the restart command, the whole wait, and between looks.
RESTART_GRACE_S = 2.0
RESTART_WAIT_S = 30.0
RESTART_POLL_S = 2.0
#: Suggestions below this score are guesses, not suggestions.
MIN_SCORE = 3
MAX_SUGGESTIONS = 3

_locks: dict[str, asyncio.Lock] = {}
#: Entries something else has in hand (the pin finder), with what to tell a caller who tries to load a template.
BUSY: dict[str, str] = {}
#: Tasmota leaves the flash pins out of a template: GPIO6, 7, 8 and 11 on the ESP8266 and the ESP32.
_FLASH_PINS = {"esp8266": (6, 7, 8, 11), "esp32": (6, 7, 8, 11), "esp32c3": (), "esp32s3": ()}
_MAX_GPIO = {"esp8266": 18, "esp32": 40, "esp32c3": 22, "esp32s3": 49}


class TemplateError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# -- templates (pure) ------------------------------------------------------------------------------

def validate_template(value: Any) -> dict[str, Any]:
    """The template as a dict, or TemplateError saying what is wrong with it.

    Takes the JSON text (what people paste) or an object. Needs NAME (text), GPIO (a list of whole
    numbers in the current format), BASE (a number); FLAG defaults to 0 as Tasmota's does.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise TemplateError("The template is empty. Paste the whole {\"NAME\":...} text.")
        try:
            value = json.loads(text)
        except ValueError:
            raise TemplateError("That is not a template: it is not valid JSON. Paste the whole "
                                "{\"NAME\":...,\"GPIO\":[...],\"FLAG\":0,\"BASE\":18} text.") from None
    if not isinstance(value, dict):
        raise TemplateError("A template is a JSON object: {\"NAME\":...,\"GPIO\":[...],\"FLAG\":0,\"BASE\":18}.")
    name = value.get("NAME")
    if not isinstance(name, str) or not name.strip():
        raise TemplateError("The template has no NAME.")
    if ";" in name:
        raise TemplateError("The template's NAME cannot contain ';'.")
    gpio = value.get("GPIO")
    if not isinstance(gpio, list) or not gpio:
        raise TemplateError("The template has no GPIO list.")
    if any(isinstance(g, bool) or not isinstance(g, int) or g < 0 for g in gpio):
        raise TemplateError("Every GPIO value in the template must be a whole number.")
    if len(gpio) not in {n for lengths in GPIO_LENGTHS.values() for n in lengths}:
        if len(gpio) == 13:
            raise TemplateError("This is the old (before Tasmota 9.1) template format, with 13 GPIO values. "
                                "Use the current format from the device's template page.")
        raise TemplateError(f"The template has {len(gpio)} GPIO values; Tasmota templates have 14 (ESP8266), "
                            "22 (ESP32-C3), 36 (ESP32) or 49 (ESP32-S3).")
    base = value.get("BASE")
    if isinstance(base, bool) or not isinstance(base, int) or base < 0:
        raise TemplateError("The template has no BASE module number.")
    flag = value.get("FLAG", 0)
    if isinstance(flag, bool) or not isinstance(flag, int) or flag < 0:
        raise TemplateError("The template's FLAG must be a whole number.")
    if ";" in json.dumps(value):
        raise TemplateError("The template cannot contain ';'.")
    if len(template_text(value)) > MAX_TEMPLATE_CHARS:
        raise TemplateError("The template is too long to send.")
    return value


def template_text(tpl: dict[str, Any]) -> str:
    """The template as Tasmota writes it: compact JSON, keys in their order, text as it is."""
    return json.dumps(tpl, separators=(",", ":"), ensure_ascii=False)


def same_template(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Whether two templates map the same pins: GPIO, FLAG and BASE (the NAME is only a label)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return (list(a.get("GPIO") or []) == list(b.get("GPIO") or []) and int(a.get("FLAG") or 0) == int(b.get("FLAG") or 0)
            and a.get("BASE") == b.get("BASE"))


def chip_family(chip: Any) -> str | None:
    """``esp8266``, ``esp32``, ``esp32c3`` or ``esp32s3`` from a chip or hardware name, else None."""
    text = re.sub(r"[^a-z0-9]", "", str(chip or "").lower())
    if text.startswith(("esp8266", "esp8285")):
        return "esp8266"
    if text.startswith("esp32c3"):
        return "esp32c3"
    if text.startswith("esp32s3"):
        return "esp32s3"
    if text.startswith("esp32"):
        return "esp32"
    return None


def fits_chip(tpl: dict[str, Any], family: str | None) -> bool:
    """Whether the template's pin count is the chip family's (unknown chip: any)."""
    if family is None or family not in GPIO_LENGTHS:
        return True
    return len(tpl.get("GPIO") or []) in GPIO_LENGTHS[family]


def gpio_numbers(family: str) -> list[int]:
    """The GPIO number of each place in a template's GPIO list for a chip family (ESP8266: 0-5, 9, 10, 12-17)."""
    flash = _FLASH_PINS[family]
    return [g for g in range(_MAX_GPIO[family]) if g not in flash]


def pwm_channels(tpl: dict[str, Any]) -> int:
    """How many PWM outputs (Tasmota's PWM1..32 and inverted PWMi1..32, codes 416-479) the template has."""
    return sum(1 for g in tpl.get("GPIO") or [] if isinstance(g, int) and 416 <= g < 480)


# -- the catalog -----------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_catalog(path: str = CATALOG_PATH) -> tuple[dict[str, Any], ...]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return tuple(data["templates"])


def catalog_entry(catalog_id: str) -> dict[str, Any] | None:
    return next((e for e in load_catalog() if e["id"] == catalog_id), None)


def public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    tpl = json.loads(entry["template"])
    return {**entry, "name": tpl.get("NAME"), "channels": pwm_channels(tpl)}


def search_catalog(kind: str | None = None, text: str | None = None) -> list[dict[str, Any]]:
    """Catalog entries of one kind and/or whose maker, model, name or notes contain every word of ``text``."""
    words = _words(text or "")
    out = []
    for e in load_catalog():
        if kind and e["kind"] != kind:
            continue
        hay = " ".join([e["manufacturer"], e["model"], e["id"], e["template"], e["kind"], e["chip"]]).lower()
        if all(w in hay for w in words):
            out.append(public_entry(e))
    return out


def catalog_matches(tpl: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Catalog entries whose template maps the same pins as ``tpl``, those with its NAME too first (several
    makers' bulbs share one map)."""
    found = [e for e in load_catalog() if same_template(json.loads(e["template"]), tpl)]
    name = (tpl or {}).get("NAME")
    return sorted(found, key=lambda e: json.loads(e["template"]).get("NAME") != name)


# -- reading a device ------------------------------------------------------------------------------

def parse_module(reply: dict[str, Any]) -> tuple[int | None, str | None]:
    """``(number, name)`` from a ``Module`` reply: ``{"Module":{"0":"Kauf Bulb"}}`` (current) or
    ``{"Module":"0 (Kauf Bulb)"}`` (older firmware)."""
    mod = reply.get("Module")
    if isinstance(mod, dict) and mod:
        key, name = next(iter(mod.items()))
        return (int(key) if str(key).isdigit() else None), (str(name) if name is not None else None)
    if isinstance(mod, int):
        return mod, None
    if isinstance(mod, str) and (m := re.match(r"\s*(\d+)\s*(?:\((.*)\))?", mod)):
        return int(m.group(1)), m.group(2)
    return None, None


def device_facts(status: dict[str, Any], template: dict[str, Any] | None, module: int | None,
                 module_name: str | None, extra_names: list[str]) -> dict[str, Any]:
    """What the suggestions and the page need from a device's replies."""
    from services.homehub.drivers.tasmota import _parse_power, has_light, light_channels

    st = status.get("Status") or {}
    sts = status.get("StatusSTS") or {}
    friendly = st.get("FriendlyName") or []
    if isinstance(friendly, str):
        friendly = [friendly]
    light = has_light(sts)
    powers = _parse_power(sts)
    relays = len(powers) - (1 if light and powers else 0)
    energy = bool((status.get("StatusSNS") or {}).get("ENERGY"))
    if module is None and isinstance(st.get("Module"), int):
        module = st["Module"]
    return {
        "device_name": st.get("DeviceName") or None,
        "friendly_name": [str(f) for f in friendly if f],
        "hostname": (status.get("StatusNET") or {}).get("Hostname") or None,
        "chip": (status.get("StatusFWR") or {}).get("Hardware") or None,
        "firmware": (status.get("StatusFWR") or {}).get("Version") or None,
        "module": module, "module_name": module_name,
        "template": template, "template_name": (template or {}).get("NAME"),
        "light": light, "light_channels": light_channels(sts) if light else 0,
        "relays": max(0, relays), "energy": energy, "uptime_s": sts.get("UptimeSec"),
        "other_names": [n for n in extra_names if n],
    }


# -- suggestions (pure) ----------------------------------------------------------------------------

_STOP = {"tasmota", "smart", "wifi", "wi", "fi", "the", "and", "of", "for", "with", "module", "esp", "device",
         "generic", "user", "my", "new"}
_KIND_WORDS = {
    "bulb": {"bulb", "bulbs", "lamp", "lamps", "light", "lights", "a15", "a19", "a21", "br30", "gu10", "e26", "e27",
             "rgbcct", "rgbww", "rgbw", "rgbcw"},
    "plug": {"plug", "plugs", "outlet", "outlets", "socket", "sockets", "receptacle"},
    "switch": {"switch", "switches", "gang", "wall"},
    "dimmer": {"dimmer", "dimmers", "dim"},
    "strip": {"strip", "strips", "ledstrip", "ws2812", "ws2812b", "rgbic", "neopixel", "controller"},
    "relay": {"relay", "relays", "shelly", "mini"},
}
_LIGHT_KINDS = {"bulb", "strip", "dimmer"}
_RELAY_KINDS = {"plug", "switch", "relay"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", str(text or "").lower()) if len(w) >= 2 and w not in _STOP]


def _device_words(facts: dict[str, Any]) -> set[str]:
    texts = [facts.get("device_name"), facts.get("hostname"), *facts.get("friendly_name", []),
             *facts.get("other_names", [])]
    tpl_name, mod_name = facts.get("template_name"), facts.get("module_name")
    # A template the device already has names what it was set up as, unless it is Tasmota's default.
    for name in (tpl_name, mod_name):
        if name and not re.match(r"(?i)^generic|^sonoff basic$", str(name)):
            texts.append(name)
    words: set[str] = set()
    for t in texts:
        words.update(_words(t or ""))
    return words


def _kind_hints(words: set[str]) -> set[str]:
    return {kind for kind, hints in _KIND_WORDS.items() if words & hints}


def score_entry(entry: dict[str, Any], facts: dict[str, Any], words: set[str] | None = None) -> tuple[int, list[str]]:
    """``(score, reasons)`` of one catalog entry for the device; ``(-1, [why])`` when it cannot fit at all."""
    tpl = json.loads(entry["template"])
    family = chip_family(facts.get("chip"))
    if family is not None and chip_family(entry["chip"]) != family:
        return -1, [f"Made for {entry['chip']}, this device is {facts.get('chip')}"]
    words = _device_words(facts) if words is None else words
    score, reasons = 0, []
    maker = _words(entry["manufacturer"])
    if maker and all(w in words for w in maker):
        score += 4
        reasons.append(f"Its name mentions {entry['manufacturer']}")
    codes = [w for w in _words(entry["model"]) + _words(entry["id"]) if any(c.isdigit() for c in w) and len(w) >= 3
             and not re.fullmatch(r"\d+w|\d+a|v\d|a\d\d", w)]
    hit = next((c for c in dict.fromkeys(codes) if c in words), None)
    if hit:
        score += 5
        reasons.append(f"Its name mentions the model {hit.upper()}")
    kind = entry["kind"]
    hints = _kind_hints(words)
    if kind in hints:
        score += 2
        reasons.append(f"Its name says it is a {kind}")
    if kind == "bulb" and "bulb" in hints and not facts.get("light"):
        score += 2
        reasons.append("Named like a light, but it drives no light channels yet: a bulb template fixes that")
    channels = pwm_channels(tpl)
    if facts.get("light") and kind in _LIGHT_KINDS:
        score += 1
        reasons.append("It already drives a light")
        if channels and facts.get("light_channels") == channels:
            score += 1
            reasons.append(f"It has {channels} light channels, as this template does")
    if facts.get("energy") and kind in ("plug", "relay") and any(2600 <= g < 2800 or 3072 <= g < 3136
                                                                 for g in tpl["GPIO"]):
        score += 1
        reasons.append("It reports power use, and this template has a power meter")
    if facts.get("relays") and not facts.get("light") and kind in _RELAY_KINDS:
        score += 1
        reasons.append("It switches relays")
    if family is not None and score:
        score += 1
        reasons.append(f"Same chip family ({entry['chip']})")
    return score, reasons


#: A measured template whose maker or model the device's names match ranks above every catalog entry.
MEASURED_MATCH_BONUS = 20


def score_measured(rec: dict[str, Any], facts: dict[str, Any], words: set[str] | None = None) -> tuple[int, list[str]]:
    """``(score, reasons)`` of a template measured in this household: the catalog's score, plus a bonus that
    puts it first when the device's names contain its whole model (or maker and a model code)."""
    words = _device_words(facts) if words is None else words
    score, reasons = score_entry({**rec, "id": ""}, facts, words)
    if score < 0:
        return score, reasons
    model = _words(rec.get("model") or "")
    maker = _words(rec.get("manufacturer") or "")
    if (model and all(w in words for w in model)) or any(r.startswith("Its name mentions the model") for r in reasons):
        score += MEASURED_MATCH_BONUS
        reasons.insert(0, f"Its name matches the model measured on {rec.get('device_name') or 'another device'}")
    elif maker and all(w in words for w in maker):
        score += 2
    return score, reasons


def suggest(facts: dict[str, Any], limit: int = MAX_SUGGESTIONS,
            measured: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Up to ``limit`` templates that fit the device best, each with its score and reasons: the catalog's and
    those measured in this household (``source: "measured"``)."""
    from services.homehub.tasmota_measured import public

    words = _device_words(facts)
    ranked = []
    for rec in measured or []:
        score, reasons = score_measured(rec, facts, words)
        if score >= MIN_SCORE:
            ranked.append((score, 0, rec["id"], {**public(rec), "score": score, "reasons": reasons}))
    for entry in load_catalog():
        score, reasons = score_entry(entry, facts, words)
        if score >= MIN_SCORE:
            ranked.append((score, 1, entry["id"], {**public_entry(entry), "source": "catalog", "score": score,
                                                   "reasons": reasons}))
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    return [r[3] for r in ranked[:limit]]


# -- the device ------------------------------------------------------------------------------------

def _tasmota_entry(runtime: Any, entry_id: str) -> Any:
    entry = runtime.hub.config_entries.get(entry_id)
    if entry is None:
        raise TemplateError("No such integration entry", 404)
    if entry.domain != "tasmota":
        raise TemplateError("This entry is not a Tasmota device", 400)
    return entry


def _driver_for(runtime: Any, entry: Any) -> tuple[Any, bool]:
    """``(driver, made_here)``: the entry's running driver, or one made just for these commands (an entry
    parked for a missing password still has an address)."""
    from services.homehub.drivers.tasmota import TasmotaDriver

    drv = runtime.hub.drivers.get(entry.entry_id)
    if isinstance(drv, TasmotaDriver):
        return drv, False
    drv = TasmotaDriver(runtime.hub, entry)
    if not drv.host:
        raise TemplateError("This Tasmota entry has no address", 409)
    return drv, True


async def _cm(drv: Any, command: str) -> dict[str, Any]:
    try:
        return await drv.cm(command)
    except AuthRequired as exc:
        raise TemplateError(f"{exc}. Enter it under Connection first.", 409) from None
    except DeviceUnavailable as exc:
        raise TemplateError(str(exc), 503) from None


def _names_of(runtime: Any, entry: Any) -> list[str]:
    names = [entry.title]
    for dev in runtime.hub.devices.values():
        if entry.entry_id in (dev.config_entries or []):
            names += [dev.name, dev.name_by_user]
    return [str(n) for n in names if n]


async def _read(runtime: Any, entry: Any, drv: Any) -> dict[str, Any]:
    template_reply = await _cm(drv, "Template")
    template: dict[str, Any] | None = None
    if isinstance(template_reply.get("GPIO"), list):
        template = template_reply
    module, module_name = parse_module(await _cm(drv, "Module"))
    status = await _cm(drv, "Status 0")
    return device_facts(status, template, module, module_name, _names_of(runtime, entry))


def _measured(runtime: Any) -> list[dict[str, Any]]:
    from services.homehub import tasmota_measured

    return tasmota_measured.all_records(runtime)


def _current(facts: dict[str, Any], measured: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    tpl = facts.get("template")
    matches = catalog_matches(tpl) if tpl else []
    mine = [r for r in measured or [] if tpl and same_template(json.loads(r["template"]), tpl)]
    return {**facts, "template_json": template_text(tpl) if tpl else None, "active": facts.get("module") == 0,
            "catalog_matches": [{"id": e["id"], "manufacturer": e["manufacturer"], "model": e["model"]}
                                for e in matches],
            "measured_matches": [{"id": r["id"], "manufacturer": r["manufacturer"], "model": r["model"],
                                  "measured_on": r.get("device_name")} for r in mine]}


#: Reasons that say something about the device itself, not only the kind of thing its name suggests.
_IDENTIFYING = re.compile(r"^(Its name mentions|It already drives a light|It has \d+ light channels|"
                          r"It reports power use|It switches relays|Its name matches)")


def drives_nothing(facts: dict[str, Any]) -> bool:
    """A device that has no light and no relays yet: a fresh flash on the Generic module, or a template with no
    pins set."""
    if facts.get("light") or facts.get("relays"):
        return False
    tpl = facts.get("template") or {}
    pins = [g for g in tpl.get("GPIO") or [] if isinstance(g, int) and g > 1]
    return not (facts.get("module") == 0 and pins)


def identified(suggestions: list[dict[str, Any]]) -> bool:
    return any(_IDENTIFYING.match(r) for s in suggestions for r in s.get("reasons") or [])


async def read_device(runtime: Any, entry_id: str) -> dict[str, Any]:
    """The device's template, module and names now, the templates it already equals, and suggestions.

    When the device drives nothing and no suggestion rests on anything but its kind (its names match no maker or
    model), the suggestions are only guesses: they are returned as ``guesses``, ``suggestions`` is empty and
    ``identified`` is false, so the page can offer the pin finder first.
    """
    entry = _tasmota_entry(runtime, entry_id)
    drv, made = _driver_for(runtime, entry)
    try:
        facts = await _read(runtime, entry, drv)
    finally:
        if made:
            await drv.async_unload()
    measured = _measured(runtime)
    current = _current(facts, measured)
    found = current["measured_matches"] or current["catalog_matches"]
    up_to_date = bool(found) and current["active"]
    if current["measured_matches"]:
        m = current["measured_matches"][0]
        message = (f"It already has the {m['manufacturer']} {m['model']} template measured on {m['measured_on']}: "
                   "nothing to do.").replace("  ", " ")
    elif found:
        message = f"It already has the {found[0]['manufacturer']} {found[0]['model']} template: nothing to do."
    else:
        message = None
    ranked = suggest(facts, measured=measured)
    blank = drives_nothing(facts)
    known = identified(ranked)
    note = None
    if blank and not known:
        note = ("It reports nothing that identifies it: it runs the "
                f"{facts.get('module_name') or 'Generic'} module with no pins set, and its names match no maker "
                "or model. Find its pins with the pin finder, or search the catalog by the model on its box.")
    return {
        "entry_id": entry.entry_id, "title": entry.title, "current": current, "up_to_date": up_to_date,
        "message": message if up_to_date else None,
        "identified": known or not blank, "identify_note": note,
        "suggestions": ranked if known or not blank else [],
        "guesses": [] if known or not blank else ranked,
        "previous_template": entry.data.get("previous_template"),
    }


async def _wait_for_restart(drv: Any, uptime_before: Any) -> tuple[bool, dict[str, Any] | None]:
    """``(restarted, last Status 0)``: polls until the device answers again after going away, or answers
    with a smaller uptime than before, for up to ``RESTART_WAIT_S``."""
    await asyncio.sleep(RESTART_GRACE_S)
    deadline = time.monotonic() + RESTART_WAIT_S
    went_away, last = False, None
    while True:
        try:
            last = await drv.cm("Status 0")
        except AuthRequired:
            raise
        except DeviceUnavailable:
            went_away = True
        else:
            up = (last.get("StatusSTS") or {}).get("UptimeSec")
            fresh = isinstance(up, int) and isinstance(uptime_before, int) and up < uptime_before
            if went_away or fresh:
                return True, last
        if time.monotonic() >= deadline:
            return False, last
        await asyncio.sleep(RESTART_POLL_S)


def _entity_plan(runtime: Any, entry: Any, status: dict[str, Any]) -> tuple[Any, list[str], list[str]]:
    """``(plan, added unique_ids, stale entity_ids)``: the light and relays the device has now that the
    entry lacks, the light's colour modes brought up to date, and what it no longer has (kept, not removed)."""
    from services.homehub.discovery import norm_mac
    from services.homehub.drivers.tasmota import color_modes, has_light, light_index
    from services.homehub.flows import Plan

    hub = runtime.hub
    mac = norm_mac((status.get("StatusNET") or {}).get("Mac")) or norm_mac(entry.data.get("mac")) or ""
    mac_up = mac.upper()
    plan = Plan(hub, entry, new_entry=False)
    mine = [e for e in hub.entities.values() if e.config_entry_id == entry.entry_id]
    by_uid = {e.unique_id: e for e in mine if e.unique_id}
    device = next((hub.devices.get(e.device_id) for e in mine if e.device_id in hub.devices), None)
    if device is None:
        device = next((d for d in hub.devices.values() if entry.entry_id in (d.config_entries or [])), None)
    sts = status.get("StatusSTS") or {}
    relays = sorted(int(k[5:] or 1) for k in sts if re.fullmatch(r"POWER\d*", k))
    wanted: list[tuple[str, str, str | None, dict[str, Any] | None]] = []
    if has_light(sts):
        light = light_index(sts)
        relays = [n for n in relays if n - 1 != light]
        wanted.append(("light", f"{mac_up}_light_light_{light}", None,
                       {"supported_color_modes": color_modes(sts)}))
    for n in relays:
        wanted.append(("switch", f"{mac_up}_switch_relay_{n - 1}", None, None))
    added = []
    for domain, uid, name, caps in wanted:
        have = by_uid.get(uid)
        if have is not None:
            if caps and have.capabilities != {**(have.capabilities or {}), **caps}:
                have.capabilities = {**(have.capabilities or {}), **caps}
                runtime.store.save_entity(have)
            continue
        if device is None or not mac_up:
            continue
        plan.add_entity(domain, uid, name, device=device, capabilities=caps)
        added.append(uid)
    wanted_uids = {w[1] for w in wanted}
    stale = [e.entity_id for e in mine if e.unique_id and re.search(r"_(light_light|switch_relay)_\d+$", e.unique_id)
             and e.unique_id not in wanted_uids]
    return plan, added, stale


async def send_and_restart(runtime: Any, entry: Any, drv: Any, tpl: dict[str, Any] | None, module: int,
                           extra: list[str], uptime_before: Any) -> tuple[bool, dict[str, Any] | None,
                                                                          dict[str, Any] | None, str]:
    """Send ``extra`` one by one, then ``Backlog Template <tpl>; Module <module>`` (no template: only the module),
    wait for the restart and read the device back: ``(restarted, last Status 0, facts after or None, sent)``."""
    for cmd in extra:
        await _cm(drv, cmd)
    sent = f"Backlog Template {template_text(tpl)}; Module {module}" if tpl else f"Module {module}"
    await _cm(drv, sent)
    log.info("tasmota %s (%s): loaded template %r (module %s), waiting for its restart", entry.title,
             entry.entry_id, (tpl or {}).get("NAME"), module)
    restarted, status = await _wait_for_restart(drv, uptime_before)
    after: dict[str, Any] | None = None
    if status is not None:
        try:
            after = await _read(runtime, entry, drv)
        except TemplateError:
            after = None
    return restarted, status, after, sent


async def reload_entry(runtime: Any, entry: Any, status: dict[str, Any] | None,
                       after: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Bring the entry's entities in line with what the device drives now and restart it:
    ``(added unique_ids, stale entity_ids)``."""
    from services.homehub.flows import FlowError, commit

    added: list[str] = []
    stale: list[str] = []
    if after is not None and status is not None:
        plan, added, stale = _entity_plan(runtime, entry, status)
        try:
            await commit(runtime, plan, restart=not entry.disabled_by)
        except FlowError as exc:
            raise TemplateError(str(exc), exc.status) from None
    elif not entry.disabled_by:
        await runtime.stop_entry(entry.entry_id)
        runtime.start_entry(entry)
    return added, stale


async def _load(runtime: Any, entry: Any, tpl: dict[str, Any], extra: list[str], *,
                module: int = 0, restore_of: str | None = None) -> dict[str, Any]:
    """Send the template (and ``extra`` first), wait for the restart, verify, record, reload."""
    if entry.entry_id in BUSY:
        raise TemplateError(BUSY[entry.entry_id], 409)
    if entry.data.get("pin_finder_backup"):
        raise TemplateError("A pin finder run on this device did not finish. Put its saved template back first "
                            "(Find my pins).", 409)
    lock = _locks.setdefault(entry.entry_id, asyncio.Lock())
    if lock.locked():
        raise TemplateError("A template is already being loaded onto this device", 409)
    async with lock:
        drv, made = _driver_for(runtime, entry)
        try:
            before = await _read(runtime, entry, drv)
            old_tpl, old_module = before.get("template"), before.get("module")
            text = template_text(tpl)
            if restore_of is None and same_template(old_tpl, tpl) and old_module == module:
                return {"status": "unchanged", "verified": True, "template": text, "sent": None,
                        "message": "The device already has this template.",
                        "current": _current(before, _measured(runtime)),
                        "previous_template": entry.data.get("previous_template")}
            # Keep what it had before anything is sent, so it can be put back even if the wait fails.
            previous = template_text(old_tpl) if old_tpl else None
            if previous is not None and not same_template(old_tpl, tpl):
                entry.data = {**entry.data, "previous_template": previous, "previous_module": old_module}
                runtime.store.save_config_entry(entry)
            restarted, status, after, sent = await send_and_restart(runtime, entry, drv, tpl, module, extra,
                                                                    before.get("uptime_s"))
        finally:
            if made:
                await drv.async_unload()
        verified = bool(after) and same_template(after.get("template"), tpl) and after.get("module") == module
        added, stale = await reload_entry(runtime, entry, status, after)
        if verified:
            message = "Loaded and verified: the device restarted with the new template."
        elif after is None:
            message = (f"The template was sent, but the device did not answer within {int(RESTART_WAIT_S)} s "
                       "of its restart, so it could not be checked.")
        else:
            message = "The template was sent, but the device reports a different one; it was not verified."
        if added:
            message += f" Added {len(added)} entit{'y' if len(added) == 1 else 'ies'}."
        return {
            "status": "applied", "verified": verified, "restarted": restarted, "sent": sent, "template": text,
            "previous_template": entry.data.get("previous_template"), "message": message,
            "entities_added": added, "entities_stale": stale,
            "current": _current(after, _measured(runtime)) if after is not None else None,
        }


def _confirmed(body: dict[str, Any]) -> None:
    if body.get("confirm") is not True:
        raise TemplateError("Nothing was sent. Loading a template restarts the device; send \"confirm\": true "
                            "to go ahead.", 400)


async def apply_template(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"template": <json text or object>}``, ``{"catalog_id"}`` or ``{"measured_id"}``, with ``"confirm": true``."""
    from services.homehub import tasmota_measured

    entry = _tasmota_entry(runtime, entry_id)
    extra: list[str] = []
    if body.get("measured_id"):
        rec = tasmota_measured.get(runtime, str(body["measured_id"]))
        if rec is None:
            raise TemplateError("No such measured template", 404)
        tpl = validate_template(rec["template"])
        extra = [str(c) for c in rec.get("commands") or []]
    elif body.get("catalog_id"):
        found = catalog_entry(str(body["catalog_id"]))
        if found is None:
            raise TemplateError("No such catalog template", 404)
        tpl = validate_template(found["template"])
        extra = [str(c) for c in found.get("commands") or []]
    elif body.get("template") is not None:
        tpl = validate_template(body["template"])
    else:
        raise TemplateError("Send a template (\"template\"), a catalog entry (\"catalog_id\") or a measured "
                            "template (\"measured_id\").")
    _confirmed(body)
    family = chip_family(_known_chip(runtime, entry))
    if not fits_chip(tpl, family):
        raise TemplateError(f"This template has {len(tpl['GPIO'])} GPIO values, which is not a template for this "
                            "device's chip.", 409)
    return await _load(runtime, entry, tpl, extra)


async def restore_previous(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Send back the template the last load replaced (``{"confirm": true}``)."""
    entry = _tasmota_entry(runtime, entry_id)
    previous = entry.data.get("previous_template")
    if not previous:
        raise TemplateError("There is no previous template to restore on this device", 409)
    _confirmed(body)
    tpl = validate_template(previous)
    module = entry.data.get("previous_module")
    return await _load(runtime, entry, tpl, [], module=module if isinstance(module, int) else 0,
                       restore_of=previous)


def _known_chip(runtime: Any, entry: Any) -> str | None:
    """The chip the hub last read off the device (its registry ``model``, ``ESP8266EX``), if any."""
    for dev in runtime.hub.devices.values():
        if entry.entry_id in (dev.config_entries or []) and dev.model:
            return str(dev.model)
    return None
