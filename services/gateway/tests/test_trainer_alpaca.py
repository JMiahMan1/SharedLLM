"""The trainer asks Alpaca what the machine has, and obeys the answer.

These tests pin the shapes Alpaca actually returns (``/admin/system`` GPU lists,
``/admin/runtime`` loaded models and queue depth, ``/admin/slots`` aggregate
counts, ``/admin/lora`` adapters), the conservative readiness verdict, and the
one rule that matters on a shared GPU: a busy host is reported, not retried.
"""
from __future__ import annotations

import pytest

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
    assert system["free_vram_mb"] is None, "no card read is unknown, not 0 MB free"


def test_a_card_reporting_zero_capacity_is_unknown_not_full():
    """A failed nvidia-smi read reports 0 total: that is not a measurement."""
    system = alpaca.clean_system({"gpu_info": [{"name": "?", "total_mb": 0, "free_mb": 0}]})

    assert system["free_vram_mb"] is None


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
        {"queued_requests": 2, "loaded_models": []},
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
    models = [{"name": "qwen3:4b"}, {"name": "qwen3:8b"}, {"name": "llama3:70b"}]

    assert alpaca.choose_model({"trainer_model": "qwen3:8b"}, models) == "qwen3:8b"
    assert alpaca.choose_model({"coding_model": "qwen3:4b"}, models) == "qwen3:4b"
    assert alpaca.choose_model({"assistant_model": "qwen3:8b"}, models) == "qwen3:8b"
    assert alpaca.choose_model({}, models) == "qwen3:4b"


def test_a_loaded_text_model_wins_over_an_unloaded_one():
    models = [
        {"name": "big:70b", "router_status": "unloaded"},
        {"name": "loaded-coder", "router_status": "loaded"},
    ]

    assert alpaca.choose_model({}, models) == "loaded-coder"


def test_an_image_model_is_never_chosen_while_a_text_model_exists():
    """Alpaca labels image models "completion" too, so the name is the signal."""
    models = [
        {"name": "Qwen-Image-2.1-Q4_K", "router_status": "unknown"},
        {"name": "qwen-image-edit-rapid-aio:q4_k", "router_status": "loaded"},
    ]

    assert alpaca.can_train("Qwen-Image-2.1-Q4_K") is False
    assert alpaca.summarise_models({"models": models})["chosen"] == ""

    models.append({"name": "kat-coder-v2", "router_status": "unloaded"})
    summary = alpaca.summarise_models({"models": models})
    assert summary["chosen"] == "kat-coder-v2"
    assert summary["chosen_trainable"] is True
    assert summary["models"][0]["name"] == "qwen-image-edit-rapid-aio:q4_k", (
        "the loaded model still heads the list, it is just not chosen"
    )


def test_a_preferred_model_matches_across_the_router_separator():
    models = [{"name": "qwen3--8b"}]

    assert alpaca.choose_model({"trainer_model": "qwen3:8b"}, models) == "qwen3--8b"


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
    assert summary["models"][0]["name"] == "qwen3:8b"  # the loaded model heads the list
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


@pytest.mark.parametrize("status", [404, 405])
async def test_waiting_for_a_slot_never_blocks_a_plain_llama_server(monkeypatch, status):
    async def no_queue(url, body, *, timeout=6.0):
        raise alpaca.AlpacaUnavailable(f"{url} answered {status}", status=status)

    monkeypatch.setattr(alpaca, "post_json", no_queue)

    answer = await alpaca.wait_for_slot({"llm_local_url": "http://plain:8080"}, timeout=1)

    assert answer["status"] == "no_queue", "a host with no queue endpoint must not refuse to train"


@pytest.mark.parametrize(
    "error",
    [
        alpaca.AlpacaUnavailable("http://h/api/queue/wait answered 500", status=500),
        alpaca.AlpacaUnavailable("http://h/api/queue/wait answered 401", status=401),
        ConnectionError("connection refused"),
        TimeoutError("slow"),
    ],
)
async def test_any_other_queue_failure_is_unavailable_not_permission(monkeypatch, error):
    async def broken(url, body, *, timeout=6.0):
        raise error

    monkeypatch.setattr(alpaca, "post_json", broken)

    answer = await alpaca.wait_for_slot({"llm_local_url": "http://llm:11434"}, timeout=1)

    assert answer["status"] == "unavailable"


async def test_waiting_for_a_slot_returns_the_verdict(monkeypatch):
    async def ready(url, body, *, timeout=6.0):
        return {"status": "ready", "slots": {"available": 2}, "wait_time": 0.4}

    monkeypatch.setattr(alpaca, "post_json", ready)
    assert (await alpaca.wait_for_slot({"llm_local_url": "http://llm:11434"}))["status"] == "ready"

    async def timeout(url, body, *, timeout=6.0):
        return {"status": "timeout", "waited": 45.0}

    monkeypatch.setattr(alpaca, "post_json", timeout)
    assert (await alpaca.wait_for_slot({"llm_local_url": "http://llm:11434"}))["status"] == "timeout"


GOOD_SYSTEM = {"free_vram_mb": 6000, "total_vram_mb": 12288}


def test_unknown_free_memory_is_not_ready():
    verdict = alpaca.readiness({"free_vram_mb": None}, {"loaded_models": []}, None)

    assert verdict["ready"] is False
    assert any("unknown" in reason for reason in verdict["reasons"])


def test_a_full_card_is_not_ready():
    verdict = alpaca.readiness({"free_vram_mb": 0, "total_vram_mb": 8192}, {"loaded_models": []}, None)

    assert verdict["ready"] is False
    assert any("only 0 MB" in reason for reason in verdict["reasons"])


def test_an_idle_card_with_no_model_loaded_is_ready():
    """No model loaded: no slots to count, and nobody to step aside for."""
    verdict = alpaca.readiness(
        GOOD_SYSTEM,
        {"loaded_models": [], "active_requests": 0, "queued_requests": 0},
        {"total": 0, "available": 0, "status": "no_model", "message": "no model loaded"},
    )

    assert verdict["ready"] is True, verdict["reasons"]


def test_busy_slots_and_running_requests_are_not_ready():
    loaded = {"loaded_models": [{"model": "qwen3:8b"}], "active_requests": 0, "queued_requests": 0}

    all_busy = alpaca.readiness(GOOD_SYSTEM, loaded, {"total": 2, "available": 0, "status": "ok"})
    assert all_busy["ready"] is False and any("busy" in r for r in all_busy["reasons"])

    running = alpaca.readiness(
        GOOD_SYSTEM, {**loaded, "active_requests": 1}, {"total": 2, "available": 1, "status": "ok"}
    )
    assert running["ready"] is False and any("running" in r for r in running["reasons"])


def test_a_loaded_model_with_unknown_slots_or_runtime_is_not_ready():
    loaded = {"loaded_models": [{"model": "qwen3:8b"}]}

    assert alpaca.readiness(GOOD_SYSTEM, loaded, None)["ready"] is False
    assert alpaca.readiness(GOOD_SYSTEM, None, {"total": 2, "available": 2})["ready"] is False


def test_a_failed_slots_probe_is_unknown_not_zero():
    assert alpaca.summarise_slots(None) is None


def test_a_preferred_image_model_is_never_chosen():
    models = [{"name": "flux-image:q8"}, {"name": "qwen3:8b"}]

    assert alpaca.choose_model({"trainer_model": "flux-image:q8"}, models) == "qwen3:8b"


def test_no_results_directory_at_all_is_an_error_not_an_empty_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_BENCHMARK_RESULTS_DIR", str(tmp_path / "missing-a"))
    monkeypatch.setenv("ALPACA_SHARED_BENCHMARK_RESULTS_DIR", str(tmp_path / "missing-b"))

    with pytest.raises(alpaca.BenchmarksUnavailable, match="ALPACA_BENCHMARK_RESULTS_DIR"):
        alpaca.benchmark_summary()
