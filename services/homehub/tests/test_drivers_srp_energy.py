"""SRP Energy driver against a fake srpenergy client (no network)."""
from __future__ import annotations

import types

import pytest

from services.homehub.drivers import srp_energy as srp
from services.homehub.drivers.srp_energy import SrpEnergyDriver, daily_usage
from services.homehub.tests.conftest import make_hub

ENTRY_ID = "01J5YSDXYK9R57Q5HTQDS96FQK"
ENTITIES = [{"entity_id": "sensor.srp_energy_home_energy_usage", "unique_id": f"{ENTRY_ID}_total_usage",
             "unit_of_measurement": "kWh", "original_device_class": "energy",
             "capabilities": {"state_class": "total_increasing"}}]
DATA = {"id": "123456789", "is_tou": True, "name": "Home", "username": "u", "password": "p"}

ROWS = [("11/10/2026", "1:00 PM", "2026-11-10T13:00:00", 1.25, 0.2),
        ("11/10/2026", "2:00 PM", "2026-11-10T14:00:00", "2.05", 0.3)]


class FakeClient:
    def __init__(self, rows=ROWS):
        self.rows = rows
        self.calls = []

    def usage(self, start, end, is_tou=False):
        self.calls.append((start, end, is_tou))
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


async def setup(client):
    hub, entry = make_hub("srp_energy", DATA, ENTITIES, entry_id=ENTRY_ID)
    driver = SrpEnergyDriver(hub, entry)
    driver._new_client = lambda: client
    return hub, driver


def test_daily_usage_sums_kwh():
    assert daily_usage(ROWS) == pytest.approx(3.3)
    assert daily_usage([]) == 0


async def test_publishes_last_day_in_kwh():
    client = FakeClient()
    hub, driver = await setup(client)
    await driver.async_setup()
    st = hub.states.get("sensor.srp_energy_home_energy_usage")
    assert st.state == "3.3"
    assert st.attributes["unit_of_measurement"] == "kWh"
    assert st.attributes["state_class"] == "total_increasing"
    start, end, is_tou = client.calls[0]
    assert is_tou is True
    assert (end - start).days == 1 and str(end.tzinfo) == "America/Phoenix"


async def test_login_failure_raises_on_setup_and_marks_unavailable(caplog):
    hub, driver = await setup(FakeClient(RuntimeError("Access denied (403) during 'login/authorize'")))
    with pytest.raises(srp.HubError):
        await driver.async_setup()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "unavailable"
    assert "SRP usage fetch failed" in caplog.text


async def test_poll_failure_does_not_raise_and_recovers():
    client = FakeClient()
    hub, driver = await setup(client)
    await driver.async_setup()
    client.rows = OSError("timeout")
    await driver.async_poll()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "unavailable"
    client.rows = ROWS[:1]
    await driver.async_poll()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "1.25"


async def test_invalid_account_id_is_a_clear_error():
    hub, entry = make_hub("srp_energy", dict(DATA, id="12"), ENTITIES)
    if srp.srp_client is None:
        pytest.skip("srpenergy not installed")
    with pytest.raises(srp.HubError):
        await SrpEnergyDriver(hub, entry).async_setup()


def test_timeout_shim_puts_a_timeout_on_every_request(monkeypatch):
    module = types.SimpleNamespace(requests=None)
    srp._with_timeouts(module)
    session = module.requests.Session()
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return "resp"

    monkeypatch.setattr(srp.requests.Session, "request", fake_request)
    assert session.post("https://example.invalid/x", data={}) == "resp"
    assert seen["timeout"] == srp.REQUEST_TIMEOUT
    assert module.requests.HTTPError is srp.requests.HTTPError
    # the real requests module is untouched
    assert srp.requests.Session is not srp._TimeoutSession
