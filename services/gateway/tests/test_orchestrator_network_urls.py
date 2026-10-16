"""Service URLs follow the network the orchestrator is running on.

NETWORK_MODE is patched by its dotted path, which resolves whichever
services.config is loaded when the test runs: other tests reload that module,
and patching a copy imported at collection time then changes nothing.
"""
from services.gateway import orchestrator

SETTINGS = {
    "rag_svc_url": "http://rag:8004",
    "bridge_rag_svc_url": "http://rag:8004",
    "host_rag_svc_url": "http://127.0.0.1:8004",
    "assistant_model": "m",
}


def test_host_network_uses_the_host_url(monkeypatch):
    """Jarvis in Talk runs inside host-networked execution, where rag:8004 does not resolve."""
    monkeypatch.setattr("services.config.NETWORK_MODE", "host")
    assert orchestrator._get(SETTINGS, "rag_svc_url") == "http://127.0.0.1:8004"


def test_bridge_network_keeps_the_docker_name(monkeypatch):
    monkeypatch.setattr("services.config.NETWORK_MODE", "bridge")
    assert orchestrator._get(SETTINGS, "rag_svc_url") == "http://rag:8004"


def test_without_a_scoped_setting_the_plain_one_is_used(monkeypatch):
    monkeypatch.setattr("services.config.NETWORK_MODE", "host")
    assert orchestrator._get({"storage_svc_url": "http://storage:8005"}, "storage_svc_url") == "http://storage:8005"


def test_other_keys_are_untouched(monkeypatch):
    monkeypatch.setattr("services.config.NETWORK_MODE", "host")
    assert orchestrator._get({"host_assistant_model": "x", "assistant_model": "m"}, "assistant_model") == "m"
