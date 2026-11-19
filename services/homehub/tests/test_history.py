"""History and logbook: the newest rows are kept, the start-time state, and HA's logbook filter. Names are made up."""
from __future__ import annotations

import functools
import json
from datetime import UTC, datetime, timedelta

from services.homehub.store import Store
from services.homehub.tests.test_core_api import SECRET, H, client, setup  # noqa: F401 (fixtures)

T0 = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)


def rows(eid: str, states: list[str], *, start: datetime = T0, step_s: float = 3600, attrs: dict | None = None):
    out = []
    for i, st in enumerate(states):
        ts = (start + timedelta(seconds=step_s * i)).timestamp()
        out.append((eid, st, json.dumps(attrs or {}), ts, ts))
    return out


def test_a_long_window_keeps_its_newest_rows_and_says_it_was_cut(tmp_path):
    store = Store(str(tmp_path / "h.db"), None)
    store.import_history_rows(rows("sensor.power_draw", [str(i) for i in range(10)]))
    got, truncated = store.history_page(None, T0, limit=4)
    assert truncated
    assert [r["state"] for r in got] == ["6", "7", "8", "9"]  # oldest first, the newest four
    got, truncated = store.history_page(None, T0, limit=10)
    assert not truncated and len(got) == 10
    assert [r["state"] for r in store.history(None, T0, limit=3)] == ["7", "8", "9"]


def test_states_at_is_the_last_row_before_the_start(tmp_path):
    store = Store(str(tmp_path / "h.db"), None)
    store.import_history_rows(rows("light.porch", ["on", "off", "on"]))
    at = store.states_at(["light.porch", "light.never"], T0 + timedelta(hours=1, minutes=30))
    assert set(at) == {"light.porch"} and at["light.porch"]["state"] == "off"


def test_rest_history_starts_with_the_state_at_start_time(client, setup):  # noqa: F811
    _hub, store, _r, _d, _a = setup
    store.import_history_rows(rows("light.kitchen", ["on", "off", "on"]))
    start = T0 + timedelta(hours=1, minutes=30)
    q = f"/api/history/period/{start.isoformat()}?filter_entity_id=light.kitchen"
    r = client.get(q, headers=H)
    series = r.json()[0]
    assert [s["state"] for s in series] == ["off", "on"]
    assert datetime.fromisoformat(series[0]["last_changed"]) == start
    assert "x-homehub-history-truncated" not in r.headers
    # HA's skip_initial_state leaves it out.
    series = client.get(q + "&skip_initial_state", headers=H).json()[0]
    assert [s["state"] for s in series] == ["on"]
    # minimal_response: the start state is the full first row, the rest are state + last_changed.
    series = client.get(q + "&minimal_response&no_attributes", headers=H).json()[0]
    assert series[0]["attributes"] == {} and series[0]["entity_id"] == "light.kitchen"
    assert set(series[1]) == {"state", "last_changed"}


def test_rest_history_keeps_the_newest_rows_and_flags_the_cut(client, setup):  # noqa: F811
    _hub, store, _r, _d, _a = setup
    store.import_history_rows(rows("light.kitchen", ["s0", "s1", "s2", "s3", "s4"]))
    store.history_page = functools.partial(Store.history_page, store, limit=2)
    r = client.get(f"/api/history/period/{T0.isoformat()}?filter_entity_id=light.kitchen", headers=H)
    assert r.headers["x-homehub-history-truncated"] == "true"
    # No start-time state across the rows left out: the series begins at the oldest row kept.
    assert [s["state"] for s in r.json()[0]] == ["s3", "s4"]


def test_logbook_leaves_out_measurements_like_ha(client, setup):  # noqa: F811
    _hub, store, _r, _d, _a = setup
    store.import_history_rows(rows("sensor.living_temperature", ["20", "21"], attrs={"unit_of_measurement": "C"}))
    store.import_history_rows(rows("sensor.energy_total", ["1", "2"], attrs={"state_class": "total_increasing"}))
    store.import_history_rows(rows("counter.cups", ["1", "2"]))
    store.import_history_rows(rows("sensor.washer_status", ["idle", "washing"]))
    store.import_history_rows(rows("light.kitchen", ["on", "off"]))
    r = client.get(f"/api/logbook/{T0.isoformat()}", headers=H)
    assert sorted({e["entity_id"] for e in r.json()}) == ["light.kitchen", "sensor.washer_status"]
    # Asked for by name, a measurement is still left out (as in HA).
    assert client.get(f"/api/logbook/{T0.isoformat()}?entity=sensor.living_temperature", headers=H).json() == []


def test_logbook_keeps_the_newest_rows(client, setup):  # noqa: F811
    _hub, store, _r, _d, _a = setup
    store.import_history_rows(rows("light.kitchen", ["s0", "s1", "s2", "s3"]))
    store.history_page = functools.partial(Store.history_page, store, limit=2)
    r = client.get(f"/api/logbook/{T0.isoformat()}?entity=light.kitchen", headers=H)
    assert [e["state"] for e in r.json()] == ["s2", "s3"]
    assert r.headers["x-homehub-history-truncated"] == "true"


def _ws(client, msg: dict) -> dict:  # noqa: F811 (the fixture by name)
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": SECRET})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "history/history_during_period", **msg})
        res = ws.receive_json()
        assert res["success"], res
        return res["result"]


def test_ws_history_has_the_start_state_and_follows_ha_flags(client, setup):  # noqa: F811
    _hub, store, _r, _d, _a = setup
    store.import_history_rows(rows("light.kitchen", ["on", "off", "on"], attrs={"brightness": 3}))
    start = T0 + timedelta(hours=1, minutes=30)
    out = _ws(client, {"start_time": start.isoformat(), "entity_ids": ["light.kitchen"]})
    series = out["light.kitchen"]
    assert [s["s"] for s in series] == ["off", "on"]
    assert series[0]["lu"] == start.timestamp() and series[0]["a"] == {"brightness": 3}
    out = _ws(client, {"start_time": start.isoformat(), "entity_ids": ["light.kitchen"],
                       "include_start_time_state": False, "minimal_response": True, "no_attributes": True})
    assert [s["s"] for s in out["light.kitchen"]] == ["on"]
    assert out["light.kitchen"][0]["a"] == {}
    out = _ws(client, {"start_time": T0.isoformat(), "entity_ids": ["light.kitchen"], "minimal_response": True})
    assert "a" in out["light.kitchen"][0] and all("a" not in s for s in out["light.kitchen"][1:])
