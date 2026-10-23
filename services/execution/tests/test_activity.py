"""The activity recorder: best-effort posts to telemetry, never raises."""
import pytest

from services.execution import activity


class _Resp:
    def __init__(self, status: int = 200):
        self.status = status


class _Post:
    def __init__(self, status: int):
        self._status = status

    async def __aenter__(self):
        return _Resp(self._status)

    async def __aexit__(self, *args):
        return False


class _Client:
    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _Post(self.status)


async def test_records_one_event(monkeypatch):
    client = _Client()
    monkeypatch.setattr("services.common.http.get_client", lambda: client)

    await activity.record("kiddo", "game", item="Trivia: blue and yellow?", action="win")

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"].endswith("/api/telemetry/activity")
    assert call["json"]["user"] == "kiddo"
    assert call["json"]["feature"] == "game"
    assert call["json"]["item"] == "Trivia: blue and yellow?"
    assert call["json"]["action"] == "win"
    assert "X-Internal-Secret" in call["headers"]


async def test_blank_user_or_feature_is_skipped(monkeypatch):
    client = _Client()
    monkeypatch.setattr("services.common.http.get_client", lambda: client)

    await activity.record("", "game")
    await activity.record("kiddo", "")

    assert client.calls == []


async def test_failures_are_swallowed(monkeypatch):
    def boom():
        raise RuntimeError("telemetry is down")

    monkeypatch.setattr("services.common.http.get_client", boom)

    await activity.record("kiddo", "game")  # must not raise


async def test_a_non_200_answer_is_quiet(monkeypatch):
    client = _Client(status=503)
    monkeypatch.setattr("services.common.http.get_client", lambda: client)

    await activity.record("kiddo", "page", item="Recipes")

    assert len(client.calls) == 1
