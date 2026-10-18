"""The watch's weather and calendar buttons answer without the LLM.

Both used to go through /api/chat: weather took 35-73 s (the watch's HTTP
timeout was shorter) and the calendar prompt could overflow the small model's
8k context. These helpers read the same sources directly.
"""
from datetime import datetime, timedelta

import pytest

from services.gateway import main as gateway_main
from services.gateway import orchestrator
from services.gateway.schemas import ResolvedCredentials


def _creds() -> ResolvedCredentials:
    return ResolvedCredentials(user="jeremiah")


def test_the_watch_phrases_are_recognised():
    assert orchestrator.is_watch_weather_query(
        "In one short sentence, what is the weather right now?"
    )
    assert orchestrator.is_watch_weather_query("what is the weather right now?")
    assert not orchestrator.is_watch_weather_query("what is the weather tomorrow?")
    assert orchestrator.is_watch_calendar_query(
        "Read my calendar events for today and tell me the next one in one short sentence."
    )
    assert orchestrator.is_watch_calendar_query(
        "In one short sentence, what is next on my calendar today?"
    )
    assert not orchestrator.is_watch_calendar_query("add lunch tomorrow")


@pytest.mark.asyncio
async def test_weather_answer_uses_the_live_ha_state(monkeypatch):
    async def fake_entities(creds):
        return [{
            "entity_id": "weather.home",
            "state": "cloudy",
            "attributes": {"temperature": 78.4, "temperature_unit": "°F"},
        }]

    monkeypatch.setattr(orchestrator, "_fetch_weather_entities", fake_entities)
    assert await orchestrator.watch_weather_answer(_creds()) == "Cloudy, 78°F."


@pytest.mark.asyncio
async def test_weather_answer_says_so_when_ha_has_no_weather(monkeypatch):
    async def fake_entities(creds):
        return []

    monkeypatch.setattr(orchestrator, "_fetch_weather_entities", fake_entities)
    assert "couldn't read the weather" in await orchestrator.watch_weather_answer(_creds())


class _Response:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self._payload = payload
        self.posted = None

    async def post(self, url, **kwargs):
        self.posted = (url, kwargs)
        return _Response(self._payload)


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc):
        return False


def _events_client(monkeypatch, events):
    client = _Client({"events": events})
    monkeypatch.setattr(
        gateway_main, "shared_http_client", lambda: _ClientContext(client)
    )
    return client


@pytest.mark.asyncio
async def test_calendar_answer_reads_and_picks_the_next_event(monkeypatch):
    start = datetime.now().astimezone() + timedelta(hours=1)
    end = start + timedelta(hours=1)
    client = _events_client(monkeypatch, [{
        "summary": "Dentist",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "all_day": False,
    }])

    answer = await orchestrator.watch_calendar_answer(_creds())

    assert answer == f"Dentist at {start.strftime('%-I:%M %p')}."
    url, kwargs = client.posted
    assert url.endswith("/execute/calendar")
    assert kwargs["json"]["action"] == "read"
    assert kwargs["json"]["range_start"]
    assert kwargs["json"]["range_end"]


@pytest.mark.asyncio
async def test_calendar_answer_reports_an_event_already_in_progress(monkeypatch):
    now = datetime.now().astimezone()
    end = now + timedelta(minutes=30)
    _events_client(monkeypatch, [{
        "summary": "Standup",
        "start_time": (now - timedelta(minutes=30)).isoformat(),
        "end_time": end.isoformat(),
        "all_day": False,
    }])

    answer = await orchestrator.watch_calendar_answer(_creds())
    assert answer.startswith("Standup until ")


@pytest.mark.asyncio
async def test_calendar_answer_handles_all_day_and_empty_days(monkeypatch):
    now = datetime.now().astimezone()
    _events_client(monkeypatch, [{
        "summary": "Conference",
        "start_time": now.replace(hour=0, minute=0, second=0).isoformat(),
        "end_time": now.replace(hour=23, minute=59, second=59).isoformat(),
        "all_day": True,
    }])
    assert await orchestrator.watch_calendar_answer(_creds()) == "Conference all day."

    _events_client(monkeypatch, [{
        "summary": "Yesterday",
        "start_time": (now - timedelta(days=1)).isoformat(),
        "end_time": (now - timedelta(days=1) + timedelta(hours=1)).isoformat(),
        "all_day": False,
    }])
    assert await orchestrator.watch_calendar_answer(_creds()) == "Nothing else on your calendar today."
