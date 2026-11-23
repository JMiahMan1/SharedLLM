"""Unit tests for GHCR pull auth fallback, stream error handling, and deferred ingress updates."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from docker.errors import APIError

from services.control_plane import main as cp_main


def test_pull_streaming_uses_ghcr_token_and_falls_back_anonymously_on_rejected_token(monkeypatch):
    seen_calls: list[dict] = []

    class _Api:
        def pull(self, tag, stream=True, decode=True, auth_config=None):
            seen_calls.append({"tag": tag, "auth": auth_config})
            if auth_config is not None:
                raise APIError("401 Unauthorized")
            return iter([{"status": "Downloaded newer image"}])

        def close(self):
            return None

    monkeypatch.setattr(cp_main, "client", SimpleNamespace(api=_Api()))
    monkeypatch.setattr(cp_main, "_github_token", lambda: "stale-token")

    lines = []
    cp_main._pull_streaming("ghcr.io/jmiahman1/sharedllm-ui:latest", lines.append)
    assert seen_calls == [
        {"tag": "ghcr.io/jmiahman1/sharedllm-ui:latest", "auth": {"username": "token", "password": "stale-token"}},
        {"tag": "ghcr.io/jmiahman1/sharedllm-ui:latest", "auth": None},
    ]
    assert lines == [{"status": "Downloaded newer image"}]


def test_pull_streaming_raises_on_stream_error_payload(monkeypatch):
    class _Api:
        def pull(self, tag, stream=True, decode=True, auth_config=None):
            return iter([{"error": "manifest unknown"}])

        def close(self):
            return None

    monkeypatch.setattr(cp_main, "client", SimpleNamespace(api=_Api()))
    monkeypatch.setattr(cp_main, "_github_token", lambda: "")

    with pytest.raises(RuntimeError, match="manifest unknown"):
        cp_main._pull_streaming("ghcr.io/jmiahman1/sharedllm-missing:latest")


def test_pull_and_restart_defers_gateway_recreate_and_returns_pending(monkeypatch):
    gateway_ctr = SimpleNamespace(
        id="deadbeef1234",
        name="sharedllm_gateway",
        status="running",
        attrs={
            "Name": "/sharedllm_gateway",
            "Image": "sha256:old-gw",
            "Config": {
                "Image": "ghcr.io/jmiahman1/sharedllm-gateway:latest",
                "Labels": {"com.docker.compose.service": "gateway", "com.docker.compose.project": "sharedllm"},
            },
            "HostConfig": {"NetworkMode": "sharedllm_default"},
            "NetworkSettings": {"Networks": {"sharedllm_default": {"Aliases": ["gateway"], "IPAddress": "172.26.0.5"}}},
        },
        labels={"com.docker.compose.service": "gateway", "com.docker.compose.project": "sharedllm"},
        image=SimpleNamespace(id="sha256:old-gw", tags=["ghcr.io/jmiahman1/sharedllm-gateway:latest"], attrs={}),
        reload=lambda: None,
    )
    scheduled: list[tuple] = []

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            scheduled.append((name, daemon, target))

        def start(self):
            return None

    monkeypatch.setattr(cp_main, "client", SimpleNamespace(
        images=SimpleNamespace(get=lambda tag: SimpleNamespace(id="sha256:new-gw")),
    ))
    monkeypatch.setattr(cp_main, "_resolve_container", lambda name: gateway_ctr)
    monkeypatch.setattr(cp_main, "_pull_streaming", lambda tag: None)
    monkeypatch.setattr(cp_main.threading, "Thread", _FakeThread)

    result = cp_main.pull_and_restart("sharedllm_gateway")
    assert result["status"] == "SUCCESS"
    assert result["updated"] is True
    assert result["pending"] is True
    assert len(scheduled) == 1
    assert scheduled[0][0] == "recreate-sharedllm_gateway"
