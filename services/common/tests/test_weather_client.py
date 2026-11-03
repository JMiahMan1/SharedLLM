"""The weather client every service uses never raises (docs/WEATHER.md)."""
from services.common import weather_client as wc


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def json(self, content_type=None):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Client:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, json, headers))
        if url not in self.routes:
            raise ConnectionError("down")
        return Resp(*self.routes[url])


KW = {"base": "http://execution:8003/", "secret": "s"}


async def test_reads_and_pushes_go_to_execution_with_the_secret():
    c = Client({
        "http://execution:8003/weather/forecast": (200, {"current": {"temp": 60}}),
        "http://execution:8003/weather/metrics": (200, {"ok": True, "day": "2026-11-02"}),
        "http://execution:8003/weather/events": (200, {"events": [{"id": "5-0", "type": "alert.started"}]}),
    })
    assert await wc.current(client=c, **KW) == {"temp": 60}
    assert await wc.push_metric("eggs", 11, unit="eggs", client=c, **KW) is True
    assert c.calls[1][3] == {"name": "eggs", "value": 11, "agg": "sum", "unit": "eggs"}
    assert c.calls[1][4] == {"X-Internal-Secret": "s"}
    events, last = await wc.events(after="4-0", client=c, **KW)
    assert last == "5-0" and events[0]["type"] == "alert.started"


async def test_failures_come_back_empty():
    c = Client({"http://execution:8003/weather/context": (502, {})})
    assert await wc.summary(client=c, **KW) == ""
    assert await wc.current(client=c, **KW) is None
    assert await wc.push_metric("eggs", 1, client=c, **KW) is False
    assert await wc.events(after="9-0", client=c, **KW) == ([], "9-0")
