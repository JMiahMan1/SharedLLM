"""Every option a driver reads from its entry is one the options API lets an admin set (``ENTRY_OPTIONS``).

``ENTRY_OPTIONS`` is a hand-kept allow-list, so this reads the drivers' source (never importing them, as some
need libraries a test machine lacks) for ``entry.options.get("key")`` / ``entry.options["key"]`` (a literal or a
module constant), in the driver module, in what it imports from the drivers package, and in the hub's
``<domain>_*.py`` helpers (``esphome_restarts.py`` keeps ``restart_log_level``). A read the allow-list lacks
is a setting no one can change: the PATCH would refuse it.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from services.homehub.core import ConfigEntry
from services.homehub.drivers import DRIVERS
from services.homehub.entries import ENTRY_OPTIONS, EntryError, check_option_keys

ROOT = pathlib.Path(__file__).resolve().parents[3]
HUB = ROOT / "services" / "homehub"


def _consts(tree: ast.Module) -> dict[str, str]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.update({t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)})
    return out


def _reads_in(node: ast.AST, consts: dict[str, str]) -> set[str]:
    def key_of(n: ast.AST) -> str | None:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.Name):
            return consts.get(n.id)
        return None

    out = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "setdefault", "pop")
                and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "options" and n.args):
            key = key_of(n.args[0])
        elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) and n.value.attr == "options":
            key = key_of(n.slice)
        else:
            continue
        if key:
            out.add(key)
    return out


def _module_reads(path: pathlib.Path, names: set[str] | None = None) -> tuple[set[str], ast.Module]:
    tree = ast.parse(path.read_text())
    consts = _consts(tree)
    if names is None:
        return _reads_in(tree, consts), tree
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            out |= _reads_in(node, consts)
    return out, tree


def options_read(domain: str) -> set[str]:
    module = DRIVERS[domain].split(":")[0]
    path = ROOT / (module.replace(".", "/") + ".py")
    found, tree = _module_reads(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("services.homehub.drivers."):
            shared = ROOT / (node.module.replace(".", "/") + ".py")
            if shared.exists() and shared != path:
                found |= _module_reads(shared, {a.name for a in node.names})[0]
    for helper in HUB.glob(f"{domain}_*.py"):
        found |= _module_reads(helper)[0]
    return found


@pytest.mark.parametrize("domain", sorted(DRIVERS))
def test_every_option_a_driver_reads_may_be_set(domain):
    missing = options_read(domain) - ENTRY_OPTIONS.get(domain, frozenset())
    assert not missing, f"{domain} reads options {sorted(missing)} that ENTRY_OPTIONS[{domain!r}] does not allow"


def test_the_scan_sees_the_esphome_restart_log_level():
    """The case that was missed: read in esphome_restarts.py through a module constant."""
    assert "restart_log_level" in options_read("esphome")
    entry = ConfigEntry("e1", "esphome", "Wall screen", data={"host": "192.0.2.10"})
    check_option_keys(entry, {"restart_log_level": "debug"})  # allowed: no error
    with pytest.raises(EntryError):
        check_option_keys(entry, {"restart_log_levle": "debug"})
