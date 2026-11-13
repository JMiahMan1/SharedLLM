"""The ESPHome builder's node configs, read-only: which YAML is which node, and a node's API key.

The builder (the ``esphome`` service) keeps one YAML per node plus ``secrets.yaml``.
The hub reads that folder through a read-only mount named by
``HOMEHUB_ESPHOME_CONFIG`` (read once at startup, like every setting), for two jobs:

* adopting an encrypted node: the key comes from the node's own config, so an
  admin does not paste it (a key that is rejected still asks for one);
* a report of which device has a config, which only has an archived one, and
  which configs match no device, so nobody edits the wrong file before a flash.

Security: a key is resolved only for the one node being adopted, never logged,
never returned by any API (the report says only whether a key exists), and the
folder is never written. Unset, the feature is off and says so; nothing is guessed.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("homehub.esphome_configs")

ENV = "HOMEHUB_ESPHOME_CONFIG"
ARCHIVE = "archive"


@dataclass(frozen=True)
class _Secret:
    name: str


@dataclass(frozen=True)
class NodeConfig:
    name: str
    file: str  # relative to the config folder
    archived: bool
    key_ref: str | _Secret | None  # a literal key or a !secret reference; never exposed

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "file": self.file, "archived": self.archived, "has_key": self.key_ref is not None}


class _Loader(yaml.SafeLoader):
    """ESPHome's tags load as markers; ``!secret`` keeps its name, nothing is followed."""


def _secret(loader: yaml.SafeLoader, node: yaml.Node) -> _Secret:
    return _Secret(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


def _opaque(loader: yaml.SafeLoader, node: yaml.Node) -> None:
    return None


_Loader.add_constructor("!secret", _secret)
for _tag in ("!include", "!lambda", "!extend", "!remove", "!include_dir_named", "!include_dir_list",
             "!include_dir_merge_list", "!include_dir_merge_named", "!env_var", "!force"):
    _Loader.add_constructor(_tag, _opaque)


def _resolve_at_startup() -> Path | None:
    value = os.getenv(ENV, "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_dir():
        log.error("%s=%s is not a folder the hub can read; node keys must be typed until it is fixed", ENV, value)
    return path


# Settings come from the environment once, when the hub starts; changing one means a restart.
CONFIG_DIR: Path | None = _resolve_at_startup()


def config_dir() -> Path | None:
    return CONFIG_DIR


def _substitute(value: Any, subs: dict[str, Any]) -> str | None:
    if not isinstance(value, str):
        return None

    def one(m: re.Match[str]) -> str:
        key = m.group(1) or m.group(2)
        return str(subs.get(key, m.group(0)))

    out = re.sub(r"\$\{(\w+)\}|\$(\w+)", one, value)
    return None if "$" in out else out


def _parse(path: Path, base: Path) -> NodeConfig | None:
    try:
        with path.open(encoding="utf-8") as f:
            doc = yaml.load(f, Loader=_Loader)
    except (OSError, yaml.YAMLError) as exc:
        # A YAML error can quote the offending line, which may hold a key: log only where it is.
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        log.warning("ESPHome config %s could not be read: %s%s", path.name, type(exc).__name__, where)
        return None
    if not isinstance(doc, dict):
        return None
    subs = doc.get("substitutions") if isinstance(doc.get("substitutions"), dict) else {}
    esphome = doc.get("esphome") if isinstance(doc.get("esphome"), dict) else {}
    name = _substitute(esphome.get("name"), subs)
    if not name:
        return None
    api = doc.get("api") if isinstance(doc.get("api"), dict) else {}
    enc = api.get("encryption") if isinstance(api.get("encryption"), dict) else {}
    key = enc.get("key")
    key_ref = key if isinstance(key, (str, _Secret)) and key else None
    rel = str(path.relative_to(base))
    return NodeConfig(name=name, file=rel, archived=rel.startswith(ARCHIVE + "/"), key_ref=key_ref)


def load_nodes(base: Path) -> list[NodeConfig]:
    """Every node config in the folder and its ``archive/``; secrets.yaml and dot files are skipped."""
    found: list[NodeConfig] = []
    for folder in (base, base / ARCHIVE):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.y*ml")):
            if path.name.startswith(".") or path.stem == "secrets":
                continue
            node = _parse(path, base)
            if node is not None:
                found.append(node)
    return found


def _secrets(base: Path) -> dict[str, Any]:
    try:
        with (base / "secrets.yaml").open(encoding="utf-8") as f:
            data = yaml.load(f, Loader=_Loader)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("the ESPHome builder's secrets.yaml could not be read: %s", type(exc).__name__)
        return {}
    return data if isinstance(data, dict) else {}


class AmbiguousConfig(Exception):
    """Two current configs claim the same node name: no key is guessed."""


def key_for(device_name: str, base: Path | None = None) -> tuple[str, str] | None:
    """``(key, file)`` from the node's current config, or None when the builder has none.

    Archived configs are history, not the node as flashed, so they are not used.
    """
    base = base or config_dir()
    if base is None or not device_name:
        return None
    current = [n for n in load_nodes(base) if n.name == device_name and not n.archived]
    if not current:
        return None
    if len(current) > 1:
        raise AmbiguousConfig(f"{len(current)} configs in the ESPHome builder are named {device_name}: "
                              + ", ".join(n.file for n in current))
    node = current[0]
    ref = node.key_ref
    if isinstance(ref, _Secret):
        value = _secrets(base).get(ref.name)
        ref = value if isinstance(value, str) and value else None
    return (ref, node.file) if ref else None


def report(hub: Any, base: Path | None = None) -> dict[str, Any]:
    """Which ESPHome entry has a current config, an archived one only, or none; and orphan configs."""
    base = base or config_dir()
    if base is None:
        return {"mounted": False, "message": f"set {ENV} to the builder's config folder (mounted read-only)"}
    if not base.is_dir():
        return {"mounted": False, "message": f"{ENV}={base} is not a folder the hub can read"}
    nodes = load_nodes(base)
    by_name: dict[str, list[NodeConfig]] = {}
    for n in nodes:
        by_name.setdefault(n.name, []).append(n)
    devices, claimed = [], set()
    for entry in hub.config_entries.values():
        if entry.domain != "esphome":
            continue
        name = str(entry.data.get("device_name") or "")
        mine = by_name.get(name, [])
        current = [n for n in mine if not n.archived]
        claimed.add(name)
        status = ("ambiguous" if len(current) > 1 else "current" if current
                  else "archived_only" if mine else "none")
        devices.append({"entry_id": entry.entry_id, "title": entry.title, "device_name": name, "status": status,
                        "configs": [n.public() for n in mine]})
    orphans = [n.public() for n in nodes if n.name not in claimed and not n.archived]
    return {"mounted": True, "devices": sorted(devices, key=lambda d: d["title"].lower()), "unmatched": orphans}
