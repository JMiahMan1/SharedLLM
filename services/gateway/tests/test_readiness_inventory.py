"""Gateway readiness checks whatever the control plane lists (no service names in code)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.gateway import readiness


class _Resp:
    def __init__(self, status: int, body: Any):
        self.status, self._body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)


class _Client:
    """Answers by URL; a URL mapped to an exception raises it."""

    def __init__(self, routes: dict[str, Any]):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        answer = self.routes[url]
        if isinstance(answer, Exception):
            raise answer
        return _Resp(*answer)


CP = "http://cp.invalid:8008"
LIST = f"{CP}/api/monitored"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(readiness, "_last", None)

    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(readiness.asyncio, "sleep", _no_sleep)


def _entry(name, **kw):
    return {"name": name, "container": f"sharedllm_{name}", "state": "running", "check": "container state", **kw}


def _run(client):
    return asyncio.run(readiness.collect(client, CP, "s", skip={"gateway", "redis"}))


def test_every_listed_service_is_reported_including_a_new_one():
    client = _Client({
        LIST: (200, {"services": [
            _entry("newsvc", health_url="http://newsvc:9000/health", check="http"),
            _entry("sidecar"),
            _entry("gateway"),
        ]}),
        "http://newsvc:9000/health": (200, {"git_sha": "abc", "start_time": 1.0}),
    })
    statuses, details, inv = _run(client)
    assert statuses == {"newsvc": "OK", "sidecar": "OK"}
    assert details["newsvc"]["git_sha"] == "abc" and details["sidecar"]["check"] == "container state"
    assert inv["source"] == "control_plane"


def test_stopped_unhealthy_and_misconfigured_are_not_ok():
    client = _Client({LIST: (200, {"services": [
        _entry("down", state="exited", exit_code=1),
        _entry("sick", docker_health="unhealthy"),
        _entry("typo", label_error="bad label"),
    ]})})
    statuses, details, _ = _run(client)
    assert statuses == {"down": "STOPPED (exited, exit 1)", "sick": "UNHEALTHY", "typo": "MISCONFIGURED"}
    assert details["typo"]["error"] == "bad label"


def test_a_failing_probe_is_reported():
    client = _Client({LIST: (200, {"services": [_entry("api", health_url="http://api:1/health")]}),
                      "http://api:1/health": (503, {})})
    assert _run(client)[0] == {"api": "ERROR (503)"}


def test_without_the_control_plane_the_last_list_is_reused_and_says_so():
    ok = _Client({LIST: (200, {"services": [_entry("sidecar")]})})
    _run(ok)
    down = _Client({LIST: OSError("refused")})
    statuses, details, inv = _run(down)
    assert statuses["sidecar"] == "OK"
    assert statuses["control_plane"] == "UNREACHABLE" and "refused" in details["control_plane"]["error"]
    assert inv["source"] == "cached" and "refused" in inv["error"]


def test_with_no_list_ever_fetched_the_failure_is_the_answer():
    statuses, _, inv = _run(_Client({LIST: (500, "boom")}))
    assert statuses == {"control_plane": "UNREACHABLE"}
    assert inv["source"] == "none" and "500" in inv["error"]


def test_no_control_plane_url_is_an_explicit_failure():
    statuses, _, inv = asyncio.run(readiness.collect(_Client({}), "", "s", skip=set()))
    assert statuses == {"control_plane": "UNREACHABLE"} and "CONTROL_PLANE_URL" in inv["error"]
