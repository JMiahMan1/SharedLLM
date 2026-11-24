"""Tasmota templates measured in this household: found by the pin finder or saved from a working device.

The bundled catalog (``data/tasmota_templates.json``) only holds templates copied from templates.blakadder.com.
A bulb that is not there (a Lotton A19 CCT, say) still has a template once someone in the house has worked it
out, and the next bulb of the same model should not need the work again. These records live in the hub's
database (``tasmota_measured`` table), never in the bundled JSON, and each says how it was found:

- ``pin_finder``: the pin finder lit one pin at a time, the person said what they saw, and the result was
  checked by eye (cool and warm, or each colour).
- ``saved_from_device``: an admin saved the template of a device that was already working.
- ``by_hand``: an admin recorded one worked out by hand (``POST /hub/tasmota/measured`` with the template).

Each record: ``id``, ``manufacturer`` and ``model`` (typed by the person), ``kind``, ``chip``, ``template``
(text), ``commands``, ``method``, ``how`` (a sentence), ``measured_at`` (a date), ``device_entry_id`` and
``device_name`` (the device it was measured on, as the household names it), ``notes``. The id comes from the
maker, model and pin map, so saving the same thing twice replaces it instead of adding a copy.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from services.homehub import tasmota_templates as tt

METHODS = ("pin_finder", "saved_from_device", "by_hand")
MAX_TEXT = 60
MAX_NOTES = 300
MAX_COMMANDS = 5

_HOW = {
    "pin_finder": "Found by the pin finder: it lit one pin at a time, asked what the light looked like, "
                  "and the result was checked by eye.",
    "saved_from_device": "Saved from a device where it was working.",
    "by_hand": "Worked out by hand on the device.",
}


def _text(value: Any, what: str, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise tt.TemplateError(f"{what} must be text.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise tt.TemplateError(f"{what} is too long (at most {limit} characters).")
    return text


def _commands(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_COMMANDS:
        raise tt.TemplateError(f"commands is a list of at most {MAX_COMMANDS} Tasmota commands.")
    out = []
    for c in value:
        if not isinstance(c, str) or not c.strip() or ";" in c or len(c) > 100:
            raise tt.TemplateError("Each command is one Tasmota command without ';' (at most 100 characters).")
        out.append(c.strip())
    return out


def record_id(manufacturer: str, model: str, tpl: dict[str, Any]) -> str:
    """``measured-<maker-model>-<pin hash>``: stable for the same maker, model and pin map."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{manufacturer} {model}".lower()).strip("-")[:40] or "device"
    pins = json.dumps([tpl.get("GPIO"), tpl.get("FLAG", 0), tpl.get("BASE")], separators=(",", ":"))
    return f"measured-{slug}-{hashlib.blake2b(pins.encode(), digest_size=4).hexdigest()}"


def make_record(*, manufacturer: Any, model: Any, kind: Any, chip: Any, template: Any, commands: Any = None,
                method: str, how: Any = None, notes: Any = None, device_entry_id: str | None = None,
                device_name: str | None = None, measured_at: Any = None) -> dict[str, Any]:
    """A checked record, or TemplateError saying what is wrong (nothing is stored)."""
    maker = _text(manufacturer, "The manufacturer")
    mdl = _text(model, "The model")
    if not mdl:
        raise tt.TemplateError("Type the model (as printed on the box or the device).")
    if kind not in tt.KINDS:
        raise tt.TemplateError("kind is one of " + ", ".join(tt.KINDS) + ".")
    if method not in METHODS:
        raise tt.TemplateError("method is one of " + ", ".join(METHODS) + ".")
    tpl = tt.validate_template(template)
    family = tt.chip_family(chip)
    if family is None:
        raise tt.TemplateError("The chip is not known (ESP8266, ESP8285, ESP32, ESP32-C3 or ESP32-S3).")
    if not tt.fits_chip(tpl, family):
        raise tt.TemplateError(f"This template has {len(tpl['GPIO'])} GPIO values, which is not a template for "
                               f"an {chip}.")
    if not any(g > 1 for g in tpl["GPIO"]):
        raise tt.TemplateError("This template sets no pins, so there is nothing to save.")
    when = measured_at if isinstance(measured_at, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", measured_at) \
        else time.strftime("%Y-%m-%d")
    return {
        "id": record_id(maker, mdl, tpl), "manufacturer": maker, "model": mdl, "kind": kind,
        "chip": {"esp8266": "ESP8266", "esp32": "ESP32", "esp32c3": "ESP32-C3", "esp32s3": "ESP32-S3"}[family],
        "template": tt.template_text(tpl), "commands": _commands(commands), "method": method,
        "how": _text(how, "how", MAX_NOTES) or _HOW[method], "notes": _text(notes, "The notes", MAX_NOTES),
        "measured_at": when, "device_entry_id": device_entry_id, "device_name": device_name,
    }


def public(rec: dict[str, Any]) -> dict[str, Any]:
    """A record in the catalog's shape (so the page can list it beside them), marked as measured here."""
    tpl = json.loads(rec["template"])
    return {
        "id": rec["id"], "manufacturer": rec["manufacturer"], "model": rec["model"], "kind": rec["kind"],
        "chip": rec["chip"], "template": rec["template"], "commands": list(rec.get("commands") or []),
        "source_url": "", "notes": rec.get("notes") or "", "name": tpl.get("NAME"),
        "channels": tt.pwm_channels(tpl), "source": "measured", "method": rec.get("method"),
        "how": rec.get("how"), "measured_at": rec.get("measured_at"), "measured_on": rec.get("device_name"),
        "device_entry_id": rec.get("device_entry_id"),
    }


def all_records(runtime: Any) -> list[dict[str, Any]]:
    return list(runtime.store.load_measured_templates())


def get(runtime: Any, rec_id: str) -> dict[str, Any] | None:
    return next((r for r in all_records(runtime) if r["id"] == rec_id), None)


def search(runtime: Any, kind: str | None = None, text: str | None = None) -> list[dict[str, Any]]:
    words = tt._words(text or "")
    out = []
    for rec in all_records(runtime):
        if kind and rec["kind"] != kind:
            continue
        hay = " ".join([rec["manufacturer"], rec["model"], rec["template"], rec["kind"], rec["chip"],
                        rec.get("device_name") or ""]).lower()
        if all(w in hay for w in words):
            out.append(public(rec))
    return out


def save(runtime: Any, rec: dict[str, Any]) -> dict[str, Any]:
    runtime.store.save_measured_template(rec)
    return public(rec)


def delete(runtime: Any, rec_id: str) -> None:
    if not runtime.store.delete_measured_template(rec_id):
        raise tt.TemplateError("No such measured template", 404)


# -- the device a record was measured on --------------------------------------------------------------

def device_name_of(runtime: Any, entry: Any) -> str:
    """The name the household uses for the entry's device (its own name for it first, then the entry title)."""
    for dev in runtime.hub.devices.values():
        if entry.entry_id in (dev.config_entries or []):
            return str(dev.name_by_user or dev.name or entry.title)
    return str(entry.title)


def find_entry(runtime: Any, *, entry_id: Any = None, device_name: Any = None) -> Any:
    """The Tasmota entry named by id, or by the device's name (entry title or device name, any case)."""
    if entry_id:
        return tt._tasmota_entry(runtime, str(entry_id))
    name = " ".join(str(device_name or "").split()).casefold()
    if not name:
        raise tt.TemplateError("Name the device it was measured on (entry_id or device_name).")
    found = []
    for entry in runtime.hub.config_entries.values():
        if entry.domain != "tasmota":
            continue
        names = {str(n).casefold() for n in tt._names_of(runtime, entry)}
        if name in names:
            found.append(entry)
    if not found:
        raise tt.TemplateError(f"No Tasmota device is named {device_name!r}", 404)
    if len(found) > 1:
        raise tt.TemplateError(f"More than one Tasmota device is named {device_name!r}; send its entry_id", 409)
    return found[0]


async def save_from_device(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """"Save this device's template": read the template the device runs now and keep it as measured on it.

    Only reads the device (``Template``, ``Module``, ``Status 0``). The device must be running its template
    (``Module 0``) and the template must set pins. If the pin finder found this template on this device, the
    record says so.
    """
    entry = tt._tasmota_entry(runtime, entry_id)
    drv, made = tt._driver_for(runtime, entry)
    try:
        facts = await tt._read(runtime, entry, drv)
    finally:
        if made:
            await drv.async_unload()
    tpl = facts.get("template")
    if not tpl or facts.get("module") != 0:
        raise tt.TemplateError("This device is not running a template of its own (it runs the "
                               f"{facts.get('module_name') or 'built-in'} module), so there is nothing to save.", 409)
    found = entry.data.get("pin_finder_result") or {}
    by_finder = isinstance(found, dict) and tt.same_template(_parsed(found.get("template")), tpl)
    kind = body.get("kind") or (found.get("kind") if by_finder else None)
    if not kind:
        kind = "bulb" if facts.get("light") else "plug" if facts.get("relays") else None
    commands = body.get("commands")
    if commands is None:
        commands = list(found.get("commands") or []) if by_finder else _template_commands(tpl)
    rec = make_record(
        manufacturer=body.get("manufacturer"), model=body.get("model"), kind=kind, chip=facts.get("chip"),
        template=tpl, commands=commands, method="pin_finder" if by_finder else "saved_from_device",
        how=found.get("how") if by_finder else f"Saved from {device_name_of(runtime, entry)}, where it was working.",
        notes=body.get("notes"), device_entry_id=entry.entry_id, device_name=device_name_of(runtime, entry),
        measured_at=found.get("measured_at") if by_finder else None)
    saved = save(runtime, rec)
    return {"status": "saved", "template": saved,
            "message": f"Saved. Devices like it will be offered this template, marked "
                       f"\"Measured on {saved['measured_on']}\"."}


async def save_given(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Record a template worked out by hand, without sending anything to the device.

    ``{"entry_id" or "device_name", "manufacturer", "model", "kind", "template", "commands"?, "how"?,
    "notes"?, "measured_at"?}``. The chip is the one the hub last read off the device.
    """
    entry = find_entry(runtime, entry_id=body.get("entry_id"), device_name=body.get("device_name"))
    chip = body.get("chip") or tt._known_chip(runtime, entry)
    rec = make_record(
        manufacturer=body.get("manufacturer"), model=body.get("model"), kind=body.get("kind"), chip=chip,
        template=body.get("template"), commands=body.get("commands"), method="by_hand", how=body.get("how"),
        notes=body.get("notes"), device_entry_id=entry.entry_id, device_name=device_name_of(runtime, entry),
        measured_at=body.get("measured_at"))
    saved = save(runtime, rec)
    return {"status": "saved", "template": saved,
            "message": f"Saved as measured on {saved['measured_on']}."}


def _parsed(text: Any) -> dict[str, Any] | None:
    try:
        return json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _template_commands(tpl: dict[str, Any]) -> list[str]:
    """The template's own ``CMND`` (Tasmota separates them with ``|``)."""
    cmnd = tpl.get("CMND")
    if not isinstance(cmnd, str):
        return []
    return [c.strip() for c in cmnd.split("|") if c.strip()][:MAX_COMMANDS]
