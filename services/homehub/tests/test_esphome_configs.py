"""The ESPHome builder's configs: keys found for the node being adopted, never exposed; the config report.

Every name, address and key here is made up.
"""
from __future__ import annotations

import json

import pytest
from aioesphomeapi import InvalidEncryptionKeyAPIError

from services.homehub import esphome_configs, flows
from services.homehub.core import ConfigEntry
from services.homehub.tests.test_flows import PSK, esphome_device, esphome_finding, rt  # noqa: F401

OTHER = "b3RoZXIta2V5LW90aGVyLWtleS1vdGhlci1rZXktMDA="


def write(folder, name, body):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def node_yaml(name_line, key_line):
    return ("substitutions:\n  name: porch-bulb\n\npackages:\n  x: github://example/pkg.yaml\n\n"
            f"esphome:\n  {name_line}\n\napi:\n  encryption:\n    {key_line}\n\n"
            "ota:\n  - platform: esphome\n    password: !secret ota\n")


@pytest.fixture
def builder(tmp_path, monkeypatch):
    base = tmp_path / "esphome"
    write(base, "porch-bulb.yaml", node_yaml("name: ${name}", "key: !secret porch_key"))
    write(base, "archive/porch-bulb.yaml", node_yaml("name: porch-bulb", f'key: "{OTHER}"'))
    write(base, "garage.yaml", node_yaml("name: garage", f'key: "{OTHER}"'))
    write(base, "secrets.yaml", f'porch_key: "{PSK}"\nota: "x"\nwifi_password: "y"\n')
    monkeypatch.setattr(esphome_configs, "CONFIG_DIR", base)
    return base


def test_the_current_config_gives_the_key_through_its_secret(builder):
    assert esphome_configs.key_for("porch-bulb") == (PSK, "porch-bulb.yaml")
    assert esphome_configs.key_for("nobody") is None


def test_two_current_configs_for_one_name_are_not_guessed_between(builder):
    write(builder, "porch-bulb-copy.yaml", node_yaml("name: porch-bulb", f'key: "{OTHER}"'))
    with pytest.raises(esphome_configs.AmbiguousConfig):
        esphome_configs.key_for("porch-bulb")


def test_unset_means_off(monkeypatch):
    monkeypatch.setattr(esphome_configs, "CONFIG_DIR", None)
    assert esphome_configs.key_for("porch-bulb") is None
    assert esphome_configs.report(None)["mounted"] is False


async def test_adopting_uses_the_builders_key_without_asking(rt, builder, monkeypatch):  # noqa: F811
    seen = []

    async def probe(host, port, psk, password=""):
        seen.append(psk)
        if psk != PSK:
            raise InvalidEncryptionKeyAPIError("bad key")
        return esphome_device()

    monkeypatch.setattr(flows, "esphome_probe", probe)
    result = await flows.adopt(rt, esphome_finding(), {})
    assert seen == [PSK] and rt.hub.config_entries[result["entry_id"]].data["noise_psk"] == PSK


async def test_a_builder_key_the_node_refuses_asks_and_names_the_file(rt, builder, monkeypatch):  # noqa: F811
    write(builder, "secrets.yaml", f'porch_key: "{OTHER}"\n')

    async def probe(host, port, psk, password=""):
        raise InvalidEncryptionKeyAPIError("bad key")

    monkeypatch.setattr(flows, "esphome_probe", probe)
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, esphome_finding(), {})
    assert "porch-bulb.yaml" in need.value.error and OTHER not in need.value.error


def test_the_report_matches_entries_and_never_carries_a_key(rt, builder):  # noqa: F811
    hub = rt.hub
    for eid, name in (("e1", "porch-bulb"), ("e2", "speaker")):
        entry = ConfigEntry(eid, "esphome", name.title(), {"host": "192.0.2.9", "noise_psk": PSK,
                                                             "device_name": name})
        hub.config_entries[eid] = entry
    rep = esphome_configs.report(hub)
    by = {d["device_name"]: d for d in rep["devices"]}
    assert by["porch-bulb"]["status"] == "current" and by["speaker"]["status"] == "none"
    assert {c["file"] for c in by["porch-bulb"]["configs"]} == {"porch-bulb.yaml", "archive/porch-bulb.yaml"}
    assert [u["name"] for u in rep["unmatched"]] == ["garage"]
    text = json.dumps(rep)
    assert PSK not in text and OTHER not in text and "has_key" in text
