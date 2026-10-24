"""The streaks endpoint reads the habits from the store."""
from __future__ import annotations

from datetime import date, timedelta


def test_the_endpoint_reads_the_store(client):
    today = date.today()
    for back in range(3):
        day = (today - timedelta(days=back)).isoformat()
        client.put(f"/checkins/{day}", json={"energy": 4})
    body = client.get(f"/streaks?day={today.isoformat()}").json()
    by_key = {habit["key"]: habit for habit in body["habits"]}
    assert by_key["checkin"]["current"] == 3
    assert by_key["logging"]["current"] == 3
    assert by_key["sleep_logged"]["current"] == 0
    assert "needs" in by_key["water_goal"]
    assert body["rule"]
