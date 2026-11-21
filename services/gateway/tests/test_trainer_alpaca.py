"""The trainer asks Alpaca what the machine has, and obeys the answer.

These tests pin the shapes Alpaca actually returns (``/admin/system`` GPU lists,
``/admin/runtime`` loaded models and queue depth, ``/admin/slots`` aggregate
counts, ``/admin/lora`` adapters), the conservative readiness verdict, and the
one rule that matters on a shared GPU: a busy host is reported, not retried.
"""
from __future__ import annotations

from services.gateway.trainer import alpaca


def test_the_proxy_base_drops_a_v1_suffix(monkeypatch):
    monkeypatch.delenv("LLM_URL", raising=False)
    assert alpaca.proxy_base({"llm_local_url": "http://llm-host:11434/v1"}) == "http://llm-host:11434"
    assert alpaca.proxy_base({"llm_local_url": "http://llm-host:11434/"}) == "http://llm-host:11434"
    assert alpaca.proxy_base({}) == ""


def test_the_proxy_base_falls_back_to_the_llm_url_environment(monkeypatch):
    """Only a caller with no resolved settings reads the environment."""
    monkeypatch.setenv("LLM_URL", "http://from-env:11434")
    assert alpaca.proxy_base(None) == "http://from-env:11434"
    assert alpaca.proxy_base({"llm_local_url": ""}) == "", (
        "an explicitly empty setting must not silently inherit LLM_URL"
    )


def test_the_system_payload_becomes_gpu_and_context_figures():
    system = alpaca.clean_system(
        {
            "hostname": "llm-host",
            "gpu_info": [
                {"name": "RTX 3060", "total_mb": 12288, "used_mb": 8000, "free_mb": 4288},
                {"name": "RTX 3060", "total_mb": 12288, "used_mb": 1000, "free_mb": 11288},
            ],
            "ram_usage": {"available_gb": 22.5},
            "llama_server_props": {"n_ctx": 16384, "n_gpu_layers": 99, "model_path": "/models/qwen3.gguf"},
        }
    )

    assert system["free_vram_mb"] == 15576
    assert system["total_vram_mb"] == 24576
    assert system["context_length"] == 16384
    assert system["ram_available_gb"] == 22.5


def test_a_gpu_error_is_not_read_as_free_memory():
    system = alpaca.clean_system({"gpu_info": {"error": "unavailable"}})

    assert system["gpus"] == []
    assert system["free_vram_mb"] == 0


def test_runtime_reports_loaded_models_and_queue_depth():
    runtime = alpaca.clean_runtime(
        {
            "loaded_models": [{"model": "qwen3:8b", "backend_model": "qwen3--8b", "expires_at": None}],
            "active_requests": {"qwen3:8b": 1},
            "queued_requests": {"qwen3:8b": 2},
            "max_loaded_models": 2,
        }
    )

    assert runtime["loaded_models"][0]["model"] == "qwen3:8b"
    assert runtime["active_requests"] == 1
    assert runtime["queued_requests"] == 2


def test_slots_accept_both_the_aggregate_and_the_per_model_shape():
    assert alpaca.summarise_slots({"total": 4, "available": 2})["available"] == 2

    per_model = alpaca.summarise_slots(
        {"slots": [{"is_processing": True}, {"is_processing": False}, {}]}
    )
    assert per_model == {"total": 3, "available": 2, "status": None, "message": None}


def test_readiness_blocks_on_low_vram_and_on_a_queue():
    blocked = alpaca.readiness(
        {"free_vram_mb": 400, "total_vram_mb": 12288},
        {"queued_requests": 2},
        {"total": 4, "available": 0, "status": "ok"},
    )
    assert blocked["ready"] is False
    assert any("GPU memory" in reason for reason in blocked["reasons"])
    assert any("queued" in reason for reason in blocked["reasons"])

    ready = alpaca.readiness(
        {"free_vram_mb": 6000, "total_vram_mb": 12288},
        {"queued_requests": 0, "loaded_models": [{"model": "qwen3:8b"}]},
        {"total": 4, "available": 3, "status": "ok"},
    )
    assert ready["ready"] is True and ready["reasons"] == []


def test_choosing_a_model_prefers_the_trainer_setting_then_coding_then_assistant():
    models = ["qwen3:4b", "qwen3:8b", "llama3:70b"]

    assert alpaca.choose_model({"trainer_model": "qwen3:8b"}, models) == "qwen3:8b"
    assert alpaca.choose_model({"coding_model": "qwen3:4b"}, models) == "qwen3:4b"
    assert alpaca.choose_model({"assistant_model": "qwen3:8b"}, models) == "qwen3:8b"
    assert alpaca.choose_model({}, models) == "qwen3:4b"


def test_a_preferred_model_that_is_not_installed_is_reported():
    summary = alpaca.summarise_models(
        {
            "models": [
                {"name": "qwen3:8b", "size": 5_000_000_000, "context_length": 32768,
                 "capabilities": ["completion"], "complete": True, "router_status": "loaded"},
                {"name": "tiny:1b", "size": 800_000_000, "context_length": 8192,
                 "capabilities": [], "complete": True, "router_status": "unloaded"},
            ]
        },
        {"trainer_model": "missing:7b"},
    )

    assert summary["total"] == 2
    assert summary["models"][0]["name"] == "qwen3:8b"  # largest context first
    assert summary["trainer_model_missing"] is True
    assert summary["chosen"] == "qwen3:8b"
    assert summary["largest_context"] == 32768


async def test_probe_reports_an_unconfigured_host_instead_of_raising(monkeypatch):
    monkeypatch.delenv("LLM_URL", raising=False)

    answer = await alpaca.probe({})

    assert answer["reachable"] is False
    assert answer["ready"] is False
    assert "llm_local_url is not set" in answer["reasons"][0]


async def test_probe_reports_an_unreachable_host_instead_of_raising(monkeypatch):
    async def broken(url, *, timeout=6.0):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(alpaca, "get_json", broken)

    answer = await alpaca.probe({"llm_local_url": "http://llm-host:11434"})

    assert answer["reachable"] is False
    assert answer["ready"] is False
    assert any("connection refused" in reason for reason in answer["reasons"])


async def test_probe_combines_system_runtime_slots_models_and_lora(monkeypatch):
    payloads = {
        "/admin/system": {
            "hostname": "llm-host",
            "gpu_info": [{"name": "RTX", "total_mb": 12288, "used_mb": 2000, "free_mb": 10288}],
            "llama_server_props": {"n_ctx": 16384},
        },
        "/admin/runtime": {"loaded_models": [{"model": "qwen3:8b"}], "queued_requests": {}},
        "/admin/slots": {"total": 4, "available": 3},
        "/admin/models": {"models": [{"name": "qwen3:8b", "context_length": 32768, "size": 5e9}]},
        "/admin/lora": {"adapters": [{"id": 0, "path": "sermons-lora.gguf", "scale": 1.0}]},
    }

    async def fake_get(url, *, timeout=6.0):
        for path, payload in payloads.items():
            if url.endswith(path):
                return payload
        raise AssertionError(f"unexpected {url}")

    monkeypatch.setattr(alpaca, "get_json", fake_get)

    answer = await alpaca.probe({"llm_local_url": "http://llm-host:11434", "trainer_model": "qwen3:8b"})

    assert answer["reachable"] is True and answer["ready"] is True
    assert answer["free_vram_mb"] == 10288
    assert answer["context_length"] == 16384
    assert answer["models"]["chosen"] == "qwen3:8b"
    assert answer["lora"]["total"] == 1


async def test_waiting_for_a_slot_never_blocks_a_plain_llama_server(monkeypatch):
    async def no_queue(url, body, *, timeout=6.0):
        raise ConnectionError("404")

    monkeypatch.setattr(alpaca, "post_json", no_queue)

    answer = await alpaca.wait_for_slot({"llm_local_url": "http://plain:8080"}, timeout=1)

    assert answer["status"] == "unavailable", "a host with no queue endpoint must not refuse to train"


async def test_waiting_for_a_slot_returns_the_verdict(monkeypatch):
    async def ready(url, body, *, timeout=6.0):
        return {"status": "ready", "slots": {"available": 2}, "wait_time": 0.4}

    monkeypatch.setattr(alpaca, "post_json", ready)
    assert (await alpaca.wait_for_slot({"llm_local_url": "http://llm:11434"}))["status"] == "ready"

    async def timeout(url, body, *, timeout=6.0):
        return {"status": "timeout", "waited": 45.0}

    monkeypatch.setattr(alpaca, "post_json", timeout)
    assert (await alpaca.wait_for_slot({"llm_local_url": "http://llm:11434"}))["status"] == "timeout"
