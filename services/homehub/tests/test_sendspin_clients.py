"""HomeHub's side of Music Assistant's Sendspin pairing (sendspin_clients.py, api.py).

The Sendspin port is the one Music Assistant streams audio through on a slow device, so a
satellite is read once and its id kept; it is read again only when it has none, after a
failed read (backing off), or when the node reconnects. Fake hosts, ids and tokens only.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

from services.homehub import api
from services.homehub import sendspin_clients as sc
from services.homehub.auth import Principal
from services.homehub.tests.test_esphome_noise_sendspin import PAIRING_TOKEN, sendspin_hub

CLIENT_ID = "Ip_made-up-client-id"


class Probe:
    """A fake Sendspin endpoint: answers per host, counts the reads."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def __call__(self, host: str) -> str | None:
        self.calls.append(host)
        answer = self.answers.get(host)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def tracker(answers: dict[str, Any] | None = None, **hub_kwargs: Any):
    hub, _entry, _conn = sendspin_hub(**hub_kwargs)
    runtime = SimpleNamespace(store=SimpleNamespace(save_entity=lambda e: None))
    probe = Probe({"192.0.2.60": CLIENT_ID} if answers is None else answers)
    clock = Clock()
    return sc.SendspinClients(hub, runtime, probe=probe, clock=clock), hub, probe, clock


def test_the_client_id_comes_from_the_init_frame():
    frame = json.dumps({"type": "client/init", "payload": {"client_id": CLIENT_ID, "version": 1,
                                                           "suite": "25519_ChaChaPoly_SHA256"}})
    assert sc.parse_client_init(frame) == CLIENT_ID
    assert sc.parse_client_init(json.dumps({"type": "server/hello", "payload": {}})) is None
    assert sc.parse_client_init("not json") is None
    assert sc.parse_client_init(json.dumps({"type": "client/init", "payload": {}})) is None


async def test_a_satellite_is_read_once_and_its_id_kept():
    clients, _hub, probe, _clock = tracker()
    await clients.refresh()
    await clients.refresh()
    await clients.refresh()
    assert clients.client_ids() == [CLIENT_ID]
    assert probe.calls == ["192.0.2.60"]  # never again once known


async def test_a_failed_read_backs_off_and_is_retried_later():
    clients, _hub, probe, clock = tracker({"192.0.2.60": OSError("refused")})
    await clients.refresh()
    await clients.refresh()
    assert probe.calls == ["192.0.2.60"]  # not again within the back-off
    clock.now += sc.BACKOFF_S[0]
    probe.answers["192.0.2.60"] = CLIENT_ID
    await clients.refresh()
    assert clients.client_ids() == [CLIENT_ID]
    assert len(probe.calls) == 2


async def test_a_reconnect_reads_again_but_a_failed_read_keeps_the_old_id():
    clients, _hub, probe, _clock = tracker()
    await clients.refresh()
    probe.answers["192.0.2.60"] = OSError("busy")
    entity_id = "sensor.echo_dot_sendspin_pairing_token"
    await clients._on_state_changed("state_changed", {
        "entity_id": entity_id,
        "old_state": SimpleNamespace(state="unavailable"),
        "new_state": SimpleNamespace(state=PAIRING_TOKEN)})
    await asyncio.sleep(0)  # the scheduled refresh
    await clients.refresh()
    assert len(probe.calls) >= 2  # it was read again
    assert clients.client_ids() == [CLIENT_ID]  # and the failure did not drop it


async def test_an_ordinary_token_update_does_not_trigger_a_read():
    clients, _hub, probe, _clock = tracker()
    await clients.refresh()
    await clients._on_state_changed("state_changed", {
        "entity_id": "sensor.echo_dot_sendspin_pairing_token",
        "old_state": SimpleNamespace(state="old-token"),
        "new_state": SimpleNamespace(state="new-token")})
    await asyncio.sleep(0)
    assert probe.calls == ["192.0.2.60"]


async def test_subscribers_get_the_list_now_and_when_it_changes():
    clients, hub, _probe, _clock = tracker()
    seen: list[list[str]] = []
    unsubscribe = clients.subscribe(seen.append)
    assert seen == [[]]
    await clients.refresh()
    assert seen == [[], [CLIENT_ID]]
    await clients.refresh()  # no change, no call
    assert seen == [[], [CLIENT_ID]]
    del hub.devices["sat1"]  # the satellite is removed
    await clients.refresh()
    assert seen[-1] == []
    unsubscribe()
    unsubscribe()  # twice is harmless


async def test_the_token_is_only_for_a_known_client():
    clients, _hub, _probe, _clock = tracker()
    await clients.refresh()
    assert clients.token_for(CLIENT_ID) == PAIRING_TOKEN
    assert clients.token_for("Ip_someone-else") is None


async def test_a_disabled_token_entity_is_turned_on_once(monkeypatch):
    reloads: list[str] = []

    async def fake_reload(runtime, entry):
        reloads.append(entry.entry_id)

    monkeypatch.setattr("services.homehub.hardware._reload", fake_reload)
    clients, hub, _probe, _clock = tracker(disabled_by="integration")
    await clients.refresh()
    await clients.refresh()
    assert hub.entities["sensor.echo_dot_sendspin_pairing_token"].disabled_by is None
    assert reloads == ["esp1"]


async def test_start_and_stop(monkeypatch):
    monkeypatch.setattr(sc, "CHECK_INTERVAL_S", 3600.0)
    clients, _hub, probe, _clock = tracker()
    await clients.start()
    await asyncio.sleep(0.01)
    await clients.stop()
    assert probe.calls == ["192.0.2.60"]


async def test_the_token_never_reaches_a_log(caplog):
    clients, _hub, _probe, _clock = tracker()
    with caplog.at_level(logging.DEBUG):
        await clients.refresh()
        clients.token_for(CLIENT_ID)
    assert PAIRING_TOKEN not in caplog.text


# -- the WebSocket commands Music Assistant sends --------------------------------------------------------------------

class FakeConn(api.WSConnection):
    def __init__(self, runtime: Any, admin: bool = True) -> None:  # no socket
        self.runtime = runtime
        self.hub = runtime.hub
        self.subs = {}
        self._tasks = set()
        self.principal = Principal(user_id="u1", name="Music Assistant", is_admin=admin)
        self.sent: list[dict[str, Any]] = []

    async def send(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)


async def test_music_assistant_follows_the_client_ids():
    clients, hub, _probe, _clock = tracker()
    conn = FakeConn(SimpleNamespace(hub=hub, sendspin_clients=clients))
    await conn.handle({"id": 7, "type": "sendspin/subscribe_clients"})
    await asyncio.sleep(0)
    assert conn.sent[0] == {"id": 7, "type": "result", "success": True, "result": None}
    assert conn.sent[1] == {"id": 7, "type": "event", "event": {"client_ids": []}}
    await clients.refresh()
    await asyncio.sleep(0)
    assert conn.sent[-1] == {"id": 7, "type": "event", "event": {"client_ids": [CLIENT_ID]}}


async def test_music_assistant_fetches_a_token_only_for_a_known_client():
    clients, hub, _probe, _clock = tracker()
    await clients.refresh()
    conn = FakeConn(SimpleNamespace(hub=hub, sendspin_clients=clients))
    await conn.handle({"id": 8, "type": "sendspin/get_pairing_token", "client_id": CLIENT_ID})
    assert conn.sent[-1]["result"] == {"token": PAIRING_TOKEN}
    await conn.handle({"id": 9, "type": "sendspin/get_pairing_token", "client_id": "Ip_unknown"})
    assert conn.sent[-1]["success"] is False and conn.sent[-1]["error"]["code"] == "not_found"


async def test_only_an_administrator_gets_an_answer():
    clients, hub, _probe, _clock = tracker()
    await clients.refresh()
    conn = FakeConn(SimpleNamespace(hub=hub, sendspin_clients=clients), admin=False)
    await conn.handle({"id": 1, "type": "sendspin/get_pairing_token", "client_id": CLIENT_ID})
    await conn.handle({"id": 2, "type": "sendspin/subscribe_clients"})
    assert [m["error"]["code"] for m in conn.sent] == ["unauthorized", "unauthorized"]
    assert PAIRING_TOKEN not in json.dumps(conn.sent)


async def test_a_hub_without_the_tracker_says_so():
    hub, _entry, _conn = sendspin_hub()
    conn = FakeConn(SimpleNamespace(hub=hub))
    await conn.handle({"id": 3, "type": "sendspin/subscribe_clients"})
    assert conn.sent[-1]["error"]["code"] == "not_supported"
