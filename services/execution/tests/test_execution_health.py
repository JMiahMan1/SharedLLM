"""What /health is allowed to measure.

Production showed `rag: unreachable after 1 attempts` with no message at all --
aiohttp's own timeout -- while RAG was answering in two milliseconds. The RAG
probe shared the pooled client that the worker's bulk traffic goes through, so
it was measuring the queue in front of RAG rather than RAG. The probe now opens
its own connection, and this pins that: the shared client is poisoned, so any
return to it fails the test.
"""

import pytest
from fastapi.testclient import TestClient

import services.execution.main as main

HEADERS = {"X-Internal-Secret": "test-secret"}


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stands in for aiohttp.ClientSession: enough to answer a health probe."""

    def __init__(self, *args, **kwargs):
        self.answered: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kwargs) -> _Response:
        self.answered.append(url)
        return _Response()


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    def _poisoned():
        raise AssertionError("the health probe must not use the shared pooled client")

    monkeypatch.setattr(main, "get_client", _poisoned)
    monkeypatch.setattr(
        main,
        "_check_service_health",
        lambda url, name: _answered(name),
    )
    monkeypatch.setattr(main, "_ping_redis", lambda: _answered("redis"))
    monkeypatch.setattr(main.aiohttp, "ClientSession", _Session)
    return TestClient(main.app)


async def _answered(name: str) -> dict:
    return {"healthy": True, "status": "ok"}


def test_the_rag_probe_does_not_wait_on_the_shared_pool(client: TestClient):
    resp = client.get("/health", headers=HEADERS)
    body = resp.json()
    assert body["checks"]["rag"] == "ok", body
    assert resp.status_code == 200, body


def test_every_dependency_still_gets_asked(client: TestClient):
    body = client.get("/health", headers=HEADERS).json()
    assert body["checks"] == {
        "redis": "ok",
        "identity": "ok",
        "rag": "ok",
        "storage": "ok",
    }
