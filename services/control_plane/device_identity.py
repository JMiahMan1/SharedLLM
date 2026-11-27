"""Identifying a device from its MAC, User-Agent, DNS lookups and hostname.

Pure helpers with no new dependency. The control plane applies their results to
the device registry (services/control_plane/network_devices.py); a value whose
source is already 'manual' is never overwritten by callers.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("control_plane.device_identity")

KINDS = ("phone", "tablet", "laptop", "desktop", "tv", "streaming", "speaker",
         "smart_home", "camera", "printer", "console", "network", "wearable",
         "other", "unknown")

_ORG_TO_MAKE = [
    ("apple", "Apple"),
    ("samsung", "Samsung"),
    ("google", "Google"),
    ("amazon", "Amazon"),
    ("roku", "Roku"),
    ("sonos", "Sonos"),
    ("lg", "LG"),
    ("sony", "Sony"),
    ("nintendo", "Nintendo"),
    ("microsoft", "Microsoft"),
    ("hp", "HP"),
    ("brother", "Brother"),
    ("espressif", "Espressif"),
    ("raspberry pi", "Raspberry Pi"),
    ("raspberrypi", "Raspberry Pi"),
    ("tp-link", "TP-Link"),
    ("tplink", "TP-Link"),
    ("ubiquiti", "Ubiquiti"),
    ("intel", "Intel"),
    ("realtek", "Realtek"),
]

_MISSING_OUI_LOGGED: set[str] = set()


def is_private_mac(mac: str | None) -> bool:
    """The locally administered bit: the second hex digit is 2, 6, A or E."""
    mac = (mac or "").strip().lower().replace("-", ":")
    if len(mac) < 2:
        return False
    return mac[1] in ("2", "6", "a", "e")


def _oui_path(path: Any = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.getenv("OUI_FILE")
    if env:
        return Path(env)
    data_dir = os.getenv("NETWORK_DATA_DIR")
    if data_dir:
        # The container mounts the network data at NETWORK_DATA_DIR (/data/network),
        # not under the working directory.
        return Path(data_dir) / "oui.csv"
    for cand in (Path("data/network/oui.csv"),
                 Path(__file__).resolve().parents[2] / "data/network/oui.csv"):
        if cand.exists():
            return cand
    return Path("data/network/oui.csv")


_OUI_CACHE: dict[str, dict[str, str]] = {}


def load_oui(path: Any = None) -> dict[str, str]:
    """Assignment prefix (6 hex) -> organization name. Empty when the file is missing."""
    p = _oui_path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        # Not cached: a file added later (scripts/update_oui.sh) is read on the next pass.
        if str(p) not in _MISSING_OUI_LOGGED:
            _MISSING_OUI_LOGGED.add(str(p))
            log.warning(f"[device-identity] OUI file missing: {p}; no vendor lookups")
        return {}
    key = f"{p}@{mtime}"
    if key in _OUI_CACHE:
        return _OUI_CACHE[key]
    _OUI_CACHE.clear()  # one file at a time; a refreshed file replaces the old table
    result: dict[str, str] = {}
    try:
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                assignment = (row.get("Assignment") or "").strip().replace(":", "").replace("-", "").lower()
                org = (row.get("Organization Name") or row.get("Organization") or
                       row.get("OrganizationName") or "").strip()
                if assignment and org:
                    result[assignment[:6]] = org
    except (OSError, csv.Error) as e:
        log.warning(f"[device-identity] could not read OUI file {p}: {e}")
    _OUI_CACHE[key] = result
    return result


def oui_organization(mac: str | None, path: Any = None) -> str | None:
    mac_norm = (mac or "").replace(":", "").replace("-", "").lower()[:6]
    if len(mac_norm) < 6:
        return None
    return load_oui(path).get(mac_norm)


def make_from_organization(org: str | None) -> str | None:
    if not org:
        return None
    low = org.lower()
    for needle, make in _ORG_TO_MAKE:
        if needle in low:
            return make
    return None


def oui_make(mac_or_org: str | None, path: Any = None) -> str | None:
    """MAC -> make via the OUI file, or an organization name -> make."""
    text = (mac_or_org or "").strip()
    if not text:
        return None
    if (":" in text or "-" in text) and len(text.replace(":", "").replace("-", "")) >= 6:
        if is_private_mac(text):
            return None
        org = oui_organization(text, path)
        return make_from_organization(org)
    return make_from_organization(text)


def oui_lookup(mac: str | None, path: Any = None) -> str | None:
    """Alias for oui_organization, for tests that call it that way."""
    return oui_organization(mac, path)


def oui_to_make(org_or_mac: str | None, path: Any = None) -> str | None:
    return oui_make(org_or_mac, path)


def make_to_kind(make: str | None) -> str | None:
    if not make:
        return None
    low = make.lower()
    if low == "espressif":
        return "smart_home"
    if low in ("tp-link", "ubiquiti"):
        return "network"
    if low == "sonos":
        return "speaker"
    return None


def _android_model(ua: str) -> str | None:
    m = re.search(r"Android\s+[^;]*;\s*([^)]*)", ua)
    if not m:
        return None
    segment = m.group(1).strip()
    if "Build/" in segment:
        segment = segment.split("Build/", 1)[0].strip()
    # Drop trailing tokens like "wv" that some UAs append.
    return segment or None


def _android_make(model: str | None) -> str | None:
    if not model:
        return None
    up = model.upper()
    if up.startswith("SM-"):
        return "Samsung"
    if up.startswith("PIXEL") or up.startswith("NEXUS"):
        return "Google"
    return None


def parse_user_agent(ua: str | None) -> dict[str, Any]:
    """Return kind/make/model/os suggestions parsed from a User-Agent string."""
    ua = ua or ""
    low = ua.lower()
    out: dict[str, Any] = {}

    if "ipad" in low:
        out.update(kind="tablet", make="Apple", os="iPadOS")
        return out
    if "iphone" in low:
        out.update(kind="phone", make="Apple", os="iOS")
        return out
    if "ipod" in low:
        out.update(kind="phone", make="Apple", os="iOS")
        return out

    if "tizen" in low:
        out.update(kind="tv", make="Samsung", os="Tizen")
        return out
    if "web0s" in low or "webos" in low:
        out.update(kind="tv", make="LG", os="webOS")
        return out
    if "roku" in low or "roku" in low:
        out.update(kind="streaming", make="Roku", os="Roku")
        return out
    if "crkey" in low:
        out.update(kind="streaming", make="Google", os="Chromecast")
        return out
    if re.search(r"\baft\w*", low):
        out.update(kind="streaming", make="Amazon", os="Fire TV")
        return out
    if "bravia" in low:
        out.update(kind="tv", make="Sony", os="Android TV")
        return out

    if "playstation" in low:
        out.update(kind="console", make="Sony", os="PlayStation")
        return out
    if "xbox" in low:
        out.update(kind="console", make="Microsoft", os="Xbox")
        return out
    if "nintendo" in low:
        out.update(kind="console", make="Nintendo", os="Nintendo")
        return out

    if "android" in low:
        model = _android_model(ua)
        mobile = "mobile" in low
        out["os"] = "Android"
        out["make"] = _android_make(model)
        out["model"] = model
        out["kind"] = "phone" if mobile else "tablet"
        return out

    if "windows" in low:
        out["os"] = "Windows"
        out["make"] = "Microsoft"
        return out
    if "cros" in low:
        out["os"] = "ChromeOS"
        out["make"] = "Google"
        return out
    if "macintosh" in low:
        # Safari on an iPad sends a Mac UA. The Jarvis app appends "[touch]"
        # (X-Device-Hint) when the screen takes touch, and no Mac has one.
        if "[touch]" in low:
            out.update(kind="tablet", make="Apple", os="iPadOS")
        else:
            out.update(make="Apple", os="macOS")
        return out

    return out


def infer_from_dns(domains: Any) -> dict[str, Any]:
    """Infer make/os/kind from the domains a device looked up in the last 24 h."""
    if isinstance(domains, (list, tuple, set)):
        texts = [str(d) for d in domains]
    elif isinstance(domains, dict):
        texts = [str(k) for k in domains]
    else:
        texts = [str(domains)] if domains is not None else []
    haystack = " ".join(texts).lower()
    out: dict[str, Any] = {}

    if any(d in haystack for d in ("apple.com", "icloud.com", "mzstatic.com")):
        out["make"] = "Apple"
    if ("connectivitycheck.gstatic.com" in haystack or
            "android.clients.google.com" in haystack):
        out["os"] = "Android"
        out["make"] = "Google"
    if any(d in haystack for d in ("msftconnecttest.com", "windowsupdate.com")):
        out["os"] = "Windows"
        out["make"] = "Microsoft"
    if "roku.com" in haystack:
        out["make"] = "Roku"
    if ("samsungcloudsolution" in haystack or "samsungotn" in haystack):
        out["make"] = "Samsung"
        out["kind"] = "tv"
    if "device-metrics-us.amazon.com" in haystack or ("amazon" in haystack and "device-metrics" in haystack):
        out["make"] = "Amazon"
    if "nintendo.net" in haystack:
        out["make"] = "Nintendo"
        out["kind"] = "console"
    if "playstation.net" in haystack:
        out["make"] = "PlayStation"
        out["kind"] = "console"
    if "xboxlive.com" in haystack:
        out["make"] = "Xbox"
        out["kind"] = "console"
    return out


def infer_from_hostname(name: str | None) -> dict[str, Any]:
    n = (name or "").lower()
    out: dict[str, Any] = {}
    if "ipad" in n:
        out.update(kind="tablet", make="Apple")
    elif "iphone" in n:
        out.update(kind="phone", make="Apple")
    elif "galaxy-tab" in n or "sm-t" in n:
        out.update(kind="tablet", make="Samsung")
    elif n.startswith("android-") or n.startswith("android_"):
        out["os"] = "Android"
    elif n.startswith("desktop-") or n.startswith("desktop_"):
        out.update(kind="desktop", os="Windows", make="Microsoft")
    elif "macbook" in n:
        out.update(kind="laptop", make="Apple")
    elif "chromecast" in n:
        out.update(kind="streaming", make="Google")
    elif "roku" in n:
        out.update(kind="streaming", make="Roku")
    elif "lgwebostv" in n:
        out.update(kind="tv", make="LG")
    elif "samsung" in n:
        out["make"] = "Samsung"
    return out


# ---------------------------------------------------------------- DHCP fingerprints

#: Windows asks for 249 (classless routes, Microsoft's number) and 252 (WPAD).
_WINDOWS_OPTIONS = {"249", "252"}


def infer_from_dhcp(vendor: str | None, options: str | None) -> dict[str, Any]:
    """OS family from a DHCP request's vendor class and requested option list ("1,3,6,...")."""
    v = (vendor or "").strip().lower()
    opts = [o for o in (options or "").split(",") if o]
    if v.startswith("android-dhcp"):
        return {"os": "Android"}
    if v.startswith("msft"):
        return {"os": "Windows"}
    if v.startswith("udhcp"):
        return {"os": "Embedded Linux"}
    if not v and opts[:2] == ["1", "121"] and "252" in opts and "119" in opts:
        # iOS, iPadOS and macOS share this order (subnet mask, then classless routes).
        return {"make": "Apple"}
    if not v and set(opts) >= _WINDOWS_OPTIONS:
        return {"os": "Windows"}
    return {}


# ---------------------------------------------------------------- mDNS (Bonjour)

#: Apple model identifiers (``model=`` in _device-info) by prefix.
_APPLE_MODELS = (
    ("ipad", "tablet", "iPadOS", "iPad"),
    ("iphone", "phone", "iOS", "iPhone"),
    ("ipod", "phone", "iOS", "iPod touch"),
    ("macbook", "laptop", "macOS", "MacBook"),
    ("imac", "desktop", "macOS", "iMac"),
    ("macmini", "desktop", "macOS", "Mac mini"),
    ("macpro", "desktop", "macOS", "Mac Pro"),
    ("mac", "desktop", "macOS", "Mac"),
    ("appletv", "streaming", "tvOS", "Apple TV"),
    ("audioaccessory", "speaker", "audioOS", "HomePod"),
    ("watch", "wearable", "watchOS", "Apple Watch"),
)


def infer_from_mdns(entry: dict[str, Any] | None) -> dict[str, Any]:
    """kind/make/model/os from what an address announced over mDNS (HomeHub's table)."""
    if not isinstance(entry, dict):
        return {}
    txt = entry.get("txt") if isinstance(entry.get("txt"), dict) else {}
    services = {str(s).lower() for s in entry.get("services") or []}
    model = str(txt.get("model") or txt.get("am") or "").strip()
    low = model.lower()
    for prefix, kind, os_name, label in _APPLE_MODELS:
        if low.startswith(prefix):
            return {"kind": kind, "make": "Apple", "os": os_name, "model": f"{label} ({model})"}
    if txt.get("md") or "_googlecast._tcp" in services:
        out: dict[str, Any] = {"kind": "streaming"}
        if txt.get("md"):
            out["model"] = str(txt["md"])
        return out
    if txt.get("ty") or txt.get("usb_MDL") or services & {"_ipp._tcp", "_printer._tcp"}:
        out = {"kind": "printer"}
        if txt.get("usb_MFG"):
            out["make"] = str(txt["usb_MFG"])
        if txt.get("ty") or txt.get("usb_MDL"):
            out["model"] = str(txt.get("ty") or txt.get("usb_MDL"))
        return out
    if "_sonos._tcp" in services:
        return {"kind": "speaker", "make": "Sonos"}
    if "_amzn-wplay._tcp" in services:
        return {"make": "Amazon"}
    if txt.get("manufacturer"):
        return {"make": str(txt["manufacturer"]), **({"model": model} if model else {})}
    if "_companion-link._tcp" in services or "_airplay._tcp" in services:
        return {"make": "Apple"}
    return {}

