"""A forwarded workspace port must be given a reachable URL, or say why not.

The URL used to be hardcoded to one machine's LAN address, so every other
install handed the UI a link to a host that does not exist.
"""
import os

import pytest

from services import workspace_sandbox


@pytest.fixture(autouse=True)
def _no_forward_threads(monkeypatch):
    """These tests never open a socket: stub the proxy out."""
    started = []

    def _fake_thread(target=None, args=(), daemon=None, **kw):
        started.append(args)
        return None

    class _Thread:
        def __init__(self, *a, **kw):
            self.start = lambda: None
            self.daemon = False

    monkeypatch.setattr(workspace_sandbox, "_run_tcp_proxy", lambda *a, **k: None)
    monkeypatch.setattr(workspace_sandbox.threading, "Thread", _Thread)
    monkeypatch.setattr(workspace_sandbox, "_get_client", lambda: None)
    monkeypatch.setattr(workspace_sandbox, "_container_name", lambda ws: f"jarvis-ws-{ws}")
    monkeypatch.setattr(workspace_sandbox, "_network_name", lambda ws: f"ws-{ws}")
    return started


def test_url_uses_the_configured_external_host(monkeypatch):
    monkeypatch.setenv("EXECUTION_EXTERNAL_HOST", "10.1.2.3")
    assert workspace_sandbox._host_forward_url(8080) == "http://10.1.2.3:8080"


def test_a_hostname_works_too(monkeypatch):
    monkeypatch.setenv("EXECUTION_EXTERNAL_HOST", "ai.example.lan")
    assert workspace_sandbox._host_forward_url(9000) == "http://ai.example.lan:9000"


def test_unset_host_is_an_error_not_a_guess(monkeypatch):
    monkeypatch.delenv("EXECUTION_EXTERNAL_HOST", raising=False)
    with pytest.raises(RuntimeError) as err:
        workspace_sandbox._host_forward_url(9000)
    # The message has to name the setting and what to put in it.
    assert "EXECUTION_EXTERNAL_HOST" in str(err.value)
    assert "LAN address" in str(err.value)


def test_blank_host_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("EXECUTION_EXTERNAL_HOST", "   ")
    with pytest.raises(RuntimeError):
        workspace_sandbox._forward_host()


def test_expose_refuses_before_it_starts_a_listener(monkeypatch):
    """A half-open forward nobody can reach is worse than a loud failure."""
    monkeypatch.delenv("EXECUTION_EXTERNAL_HOST", raising=False)
    calls = []
    monkeypatch.setattr(
        workspace_sandbox, "_run_tcp_proxy", lambda *a, **k: calls.append(a) or None
    )
    with pytest.raises(RuntimeError):
        workspace_sandbox.expose_workspace_port("ws-1", container_port=3000, host_port=9001)
    assert calls == [], "the TCP proxy was started despite the configuration error"
