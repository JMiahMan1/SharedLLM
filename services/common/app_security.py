"""Local security scan of the apps on a child's device (docs/APP_VETTING.md).

Everything here works on what the device already reported (its installer,
the permissions and special access it holds, its signing certificates and
the fingerprint of its app file) plus a bundled list; nothing new leaves the
house to make these findings. Each finding has a severity on content
monitoring's scale and one plain sentence a parent understands:

- **sideloaded**: the app did not come from Google Play (installer unknown,
  or another store).
- **risky_access**: access that stalkerware and scams rely on: an
  accessibility service, device administration, reading notifications, text
  messages, the call log, drawing over other apps, a VPN, installing other
  apps, background location, or the camera, microphone and location together.
- **stalkerware**: the package name or a signing certificate is on the
  open-source Stalkerware Indicators list (``data/stalkerware_indicators.json``,
  CC BY 4.0, refreshed with ``scripts/refresh_stalkerware_indicators.py``).
- **malware**: the app file's SHA-256 is a known sample on abuse.ch
  MalwareBazaar (looked up by telemetry; see ``malwarebazaar``).

Jarvis itself is never scanned. Pure: no I/O but reading the bundled list once.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PLAY_INSTALLER = "com.android.vending"

#: What a device reports an app declares or asks for (``capabilities``), in
#: the words the Android reporter uses (``AppInventoryRules.capabilities``).
CAPABILITIES = (
    "accessibility", "device_admin", "notification_listener", "vpn", "sms_read", "sms_send", "call_log",
    "background_location", "overlay", "install_apps",
)
#: Special access a person switched on for the app (``special_access``): these
#: are not permissions, Android keeps them in settings.
SPECIAL_ACCESS = ("accessibility", "device_admin", "notification_listener")

#: Each risky capability: (severity when only declared or asked for, severity
#: when switched on, short words, the sentence a parent reads).
RISKY: dict[str, tuple[str, str, str, str]] = {
    "accessibility": ("medium", "high", "accessibility access",
                      "It can read and control everything on the screen (accessibility service)"),
    "device_admin": ("medium", "high", "device administration",
                     "It can become a device administrator, which makes it hard to remove"),
    "notification_listener": ("medium", "high", "reading notifications",
                              "It can read every notification, including messages from other apps"),
    "sms_read": ("high", "high", "reading text messages", "It can read text messages"),
    "sms_send": ("high", "high", "sending text messages", "It can send text messages, which can cost money"),
    "call_log": ("high", "high", "the call log", "It can read who was called and when (the call log)"),
    "background_location": ("high", "high", "background location", "It can track location even when it is closed"),
    "overlay": ("medium", "medium", "drawing over other apps", "It can draw over other apps"),
    "vpn": ("medium", "medium", "a VPN", "It can route the phone's internet traffic through itself (VPN)"),
    "install_apps": ("medium", "medium", "installing other apps", "It can install other apps"),
}
assert set(RISKY) == set(CAPABILITIES)

#: Permission groups (``permissions``) that together are a tracking profile.
TRACKING_TRIO = ("camera", "microphone", "location")

LEVELS = ("medium", "high", "severe")
_SHA1 = re.compile(r"^[0-9A-F]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
STALKERWARE_FILE = Path(__file__).resolve().parent / "data" / "stalkerware_indicators.json"

STORE_NAMES = {
    PLAY_INSTALLER: "Google Play",
    "com.sec.android.app.samsungapps": "Galaxy Store",
    "com.amazon.venezia": "Amazon Appstore",
    "org.fdroid.fdroid": "F-Droid",
    "com.huawei.appmarket": "AppGallery",
}


def clean_sha1(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace(":", "").strip().upper()
    return value if _SHA1.match(value) else None


def clean_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _SHA256.match(value) else None


# --- the stalkerware list ---------------------------------------------------


@lru_cache(maxsize=1)
def stalkerware_index() -> dict[str, Any]:
    """The bundled list as lookups: package -> name, certificate -> name, and
    where it came from. A missing or unreadable file raises (the build ships it)."""
    data = json.loads(STALKERWARE_FILE.read_text(encoding="utf-8"))
    packages: dict[str, str] = {}
    certificates: dict[str, str] = {}
    for app in data.get("apps") or []:
        name = str(app.get("name") or "Unnamed")
        for package in app.get("packages") or []:
            packages.setdefault(package, name)
        for cert in app.get("certificates_sha1") or []:
            clean = clean_sha1(cert)
            if clean:
                certificates.setdefault(clean, name)
    if not packages:
        raise ValueError(f"{STALKERWARE_FILE} has no stalkerware packages")
    return {
        "packages": packages,
        "certificates": certificates,
        "source": {k: data.get(k) for k in ("source", "commit", "commit_date", "license", "attribution")},
    }


def stalkerware_source() -> dict[str, Any]:
    """Where the bundled list came from (for the card and the doc)."""
    return dict(stalkerware_index()["source"])


def match_stalkerware(package: str, certs_sha1: list[str] | None = None) -> dict[str, Any] | None:
    """``{"name", "matched": "package" | "certificate"}`` when the app is on
    the list, else None. Package names match exactly (the list names whole
    packages); certificates by their SHA-1 fingerprint."""
    index = stalkerware_index()
    name = index["packages"].get(package)
    if name:
        return {"name": name, "matched": "package"}
    for cert in certs_sha1 or []:
        clean = clean_sha1(cert)
        if clean and clean in index["certificates"]:
            return {"name": index["certificates"][clean], "matched": "certificate"}
    return None


# --- the scan ---------------------------------------------------------------


def _worst(levels: list[str]) -> str | None:
    known = [lv for lv in levels if lv in LEVELS]
    return max(known, key=LEVELS.index) if known else None


def sideload_finding(app: dict[str, Any]) -> dict[str, Any] | None:
    """Where an installed app came from, when that is not Google Play. An app
    asked for but not installed has no ``installer`` key and no finding."""
    if "installer" not in app:
        return None
    installer = app.get("installer")
    if installer == PLAY_INSTALLER:
        return None
    if not installer:
        return {"check": "sideloaded", "key": "unknown_installer", "severity": "medium",
                "text": "Not installed from Google Play, and Android does not say where it came from"}
    store = STORE_NAMES.get(installer)
    if store:
        return {"check": "sideloaded", "key": "other_store", "severity": "medium", "installer": installer,
                "text": f"Installed from {store}, not Google Play"}
    return {"check": "sideloaded", "key": "other_installer", "severity": "medium", "installer": installer,
            "text": f"Installed by another app ({installer}), not Google Play"}


def access_findings(app: dict[str, Any]) -> list[dict[str, Any]]:
    """One finding per risky kind of access the app holds or asks for."""
    caps = set(app.get("capabilities") or [])
    active = set(app.get("special_access") or [])
    out = []
    for key in CAPABILITIES:
        if key not in caps and key not in active:
            continue
        declared_level, active_level, _words, sentence = RISKY[key]
        on = key in active
        out.append({
            "check": "risky_access", "key": key, "active": on,
            "severity": active_level if on else declared_level,
            "text": sentence + (", and it is switched on" if on and key in SPECIAL_ACCESS else ""),
        })
    groups = set(app.get("permissions") or [])
    if all(g in groups for g in TRACKING_TRIO):
        out.append({"check": "risky_access", "key": "camera_microphone_location", "active": False,
                    "severity": "medium", "text": "It can use the camera, the microphone and location together"})
    return out


def malware_finding(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """A MalwareBazaar hit on the app file (the app's ``malware_check``)."""
    if not result or result.get("state") != "malware":
        return None
    what = result.get("signature") or "a known malware sample"
    return {"check": "malware", "key": "malwarebazaar", "severity": "severe", "signature": result.get("signature"),
            "text": f"Its app file is a known malware sample on MalwareBazaar ({what})"}


def scan(app: dict[str, Any], malware: dict[str, Any] | None = None,
         own_package: str | None = None) -> list[dict[str, Any]]:
    """Every finding for one app, most serious first. Jarvis has none."""
    if own_package and app.get("package") == own_package:
        return []
    findings: list[dict[str, Any]] = []
    hit = match_stalkerware(app.get("package") or "", app.get("cert_sha1") or [])
    if hit:
        how = "its package name" if hit["matched"] == "package" else "its signing certificate"
        findings.append({"check": "stalkerware", "key": hit["matched"], "severity": "severe", "name": hit["name"],
                         "text": f"Matches known stalkerware ({hit['name']}) by {how}: an app made to secretly "
                                 "watch a phone"})
    found = malware_finding(malware)
    if found:
        findings.append(found)
    side = sideload_finding(app)
    if side:
        findings.append(side)
    findings.extend(access_findings(app))
    return sorted(findings, key=lambda f: -LEVELS.index(f["severity"]))


def flags(app: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The findings that become verdict flags (and so notices): a stalkerware
    match and a malware hit (severe), and an app from outside Google Play
    holding risky access (severe when that access is switched on, else high).
    Other findings stay on the card."""
    out = []
    for f in findings:
        if f["check"] in ("stalkerware", "malware"):
            out.append({"reason": f["check"], "category": "security", "level": "severe", "text": f["text"]})
    side = next((f for f in findings if f["check"] == "sideloaded"), None)
    risky = [f for f in findings if f["check"] == "risky_access"]
    if side and risky:
        active = any(f.get("active") and f["key"] in SPECIAL_ACCESS for f in risky)
        words = []
        for f in risky:
            word = RISKY[f["key"]][2] if f["key"] in RISKY else "camera, microphone and location"
            words.append(word + (" (switched on)" if f.get("active") and f["key"] in SPECIAL_ACCESS else ""))
        where = "Not from Google Play" if side["key"] == "unknown_installer" else side["text"].split(",")[0]
        out.append({"reason": "sideloaded_risky", "category": "security", "level": "severe" if active else "high",
                    "text": f"{where}, and it has {', '.join(words[:4])}"})
    return out

