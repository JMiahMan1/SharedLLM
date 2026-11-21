"""Alpaca is the machine that hosts the household's model.

The trainer does not own the GPU. Alpaca does: the Ollama/OpenAI-compatible
proxy on 11434 (``llm_local_url``) holds the models, the slots and the LoRA
adapters, and its dashboard on 5000 keeps the benchmark results. This module
asks that machine what it has and whether a run may start at all.

Two rules shape it:

* **Ask, never assume.** Every call is a probe whose failure is data -- the
  trainer must still work when ``llm_local_url`` points at a plain llama.cpp
  server with no ``/admin`` surface.
* **The household comes first.** A pass waits for a free slot
  (``/api/queue/wait``) and steps aside when the GPU is exhausted, instead of
  burning its budget on timeouts. That is how a small machine gets trained on
  without anybody noticing.

Benchmark results are read from disk, not over HTTP: Alpaca already writes one
file per model (``data/llm_benchmarks/models/general_<model>.json`` and
``data/shared_llm_benchmarks/models/shared_<model>.json``), which is the same
data ``POST /rag/benchmark/ingest-results`` scores lessons against.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any

import aiohttp

log = logging.getLogger("gateway.trainer.alpaca")

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: The sibling checkout: ``../alpaca`` next to ``SharedLLM``.
ALPACA_ROOT = ROOT.parent / "alpaca"

#: Admin probes answer from llama-server's own state; a slow one is a problem.
PROBE_TIMEOUT_S = 6.0

#: Free VRAM below this is not enough for one more model call.
MIN_FREE_VRAM_MB = 1200

#: A benchmark category below this is worth a curriculum proposal.
WEAK_BENCHMARK_SCORE = 60


class AlpacaUnavailable(RuntimeError):
    """The Alpaca proxy did not answer."""


def proxy_base(settings: dict[str, Any] | None = None) -> str:
    """The proxy root: ``llm_local_url`` minus a trailing ``/v1``.

    ``settings`` is the resolved Identity settings when the caller has them. Only
    when nothing was resolved (a script, a cold probe) does this fall back to the
    ``LLM_URL`` environment variable: mixing the two would let an unset setting
    silently inherit a stale environment value.
    """
    if settings is None:
        source: Any = os.getenv("LLM_URL")
    else:
        source = settings.get("llm_local_url")
    url = str(source or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url


def dashboard_base() -> str:
    """The dashboard root (``ALPACA_WEB_URL``), or ``""`` when unset."""
    return (os.getenv("ALPACA_WEB_URL") or "").strip().rstrip("/")


def results_dirs() -> dict[str, pathlib.Path]:
    """Where Alpaca writes benchmark results, overridable like RAG's own paths."""
    return {
        "general": pathlib.Path(
            os.getenv("ALPACA_BENCHMARK_RESULTS_DIR")
            or ALPACA_ROOT / "data" / "llm_benchmarks" / "models"
        ),
        "shared": pathlib.Path(
            os.getenv("ALPACA_SHARED_BENCHMARK_RESULTS_DIR")
            or ALPACA_ROOT / "data" / "shared_llm_benchmarks" / "models"
        ),
    }


def _headers() -> dict[str, str]:
    key = (os.getenv("ALPACA_API_KEY") or "").strip()
    return {"X-API-Key": key} if key else {}


async def get_json(url: str, *, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """GET one admin/probe URL. The single seam tests replace."""
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout), headers=_headers()
    ) as client, client.get(url) as resp:
        if resp.status >= 400:
            raise AlpacaUnavailable(f"{url} answered {resp.status}")
        data = await resp.json(content_type=None)
        return data if isinstance(data, dict) else {"data": data}


async def post_json(url: str, body: dict[str, Any], *, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout), headers=_headers()
    ) as client, client.post(url, json=body) as resp:
        if resp.status >= 400:
            raise AlpacaUnavailable(f"{url} answered {resp.status}")
        data = await resp.json(content_type=None)
        return data if isinstance(data, dict) else {"data": data}


async def wait_for_slot(
    settings: dict[str, Any] | None, timeout: float = 45.0
) -> dict[str, Any]:
    """Block until llama-server has a free slot, or give up.

    Returns ``{"status": "ready"|"timeout"|"unavailable", ...}``. ``unavailable``
    means the proxy has no queue endpoint at all (a plain llama.cpp server), so
    the caller should proceed rather than refuse to train.
    """
    base = proxy_base(settings)
    if not base:
        return {"status": "unavailable", "reason": "llm_local_url is not set"}
    try:
        answer = await post_json(f"{base}/api/queue/wait", {"timeout": timeout}, timeout=timeout + 5.0)
    except Exception as exc:  # no queue endpoint, or the host is down
        return {"status": "unavailable", "reason": str(exc)}
    status = str(answer.get("status") or "").lower()
    if status == "ready":
        return {"status": "ready", "slots": answer.get("slots"), "wait_time": answer.get("wait_time")}
    return {"status": "timeout", "waited": answer.get("waited"), "slots": answer.get("slots")}


def clean_system(payload: dict[str, Any] | None) -> dict[str, Any]:
    """GPU/RAM/context figures from ``/admin/system``, in one predictable shape."""
    payload = payload or {}
    gpus = payload.get("gpu_info")
    if not isinstance(gpus, list):
        gpus = []
    props = payload.get("llama_server_props") if isinstance(payload.get("llama_server_props"), dict) else {}
    ram = payload.get("ram_usage") if isinstance(payload.get("ram_usage"), dict) else {}
    return {
        "hostname": payload.get("hostname"),
        "gpus": gpus,
        "free_vram_mb": sum(int(gpu.get("free_mb") or 0) for gpu in gpus),
        "total_vram_mb": sum(int(gpu.get("total_mb") or 0) for gpu in gpus),
        "ram_available_gb": ram.get("available_gb"),
        "context_length": props.get("n_ctx"),
        "gpu_layers": props.get("n_gpu_layers"),
        "model_path": props.get("model_path"),
    }


def clean_runtime(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Loaded models and queue depth from ``/admin/runtime``."""
    payload = payload or {}
    loaded = [item for item in (payload.get("loaded_models") or []) if isinstance(item, dict)]
    active = payload.get("active_requests") or {}
    queued = payload.get("queued_requests") or {}
    return {
        "loaded_models": [
            {
                "model": item.get("model") or item.get("backend_model") or item.get("name"),
                "backend_model": item.get("backend_model"),
                "expires_at": item.get("expires_at"),
            }
            for item in loaded
        ],
        "active_requests": sum(int(v or 0) for v in active.values()) if isinstance(active, dict) else 0,
        "queued_requests": sum(int(v or 0) for v in queued.values()) if isinstance(queued, dict) else 0,
        "max_loaded_models": payload.get("max_loaded_models"),
    }


def summarise_slots(payload: dict[str, Any] | None) -> dict[str, Any]:
    """``/admin/slots`` reports either aggregate counts or a per-model list."""
    payload = payload or {}
    total = int(payload.get("total") or 0)
    available = payload.get("available")
    if available is None:
        slots = [slot for slot in (payload.get("slots") or []) if isinstance(slot, dict)]
        available = sum(1 for slot in slots if not slot.get("is_processing"))
        total = total or len(slots)
    return {
        "total": total,
        "available": int(available or 0),
        "status": payload.get("status"),
        "message": payload.get("message"),
    }


def choose_model(settings: dict[str, Any] | None, models: list[str]) -> str:
    """Which model the trainer would use: its own, then coding, then assistant."""
    settings = settings or {}
    for key in ("trainer_model", "coding_model", "assistant_model"):
        wanted = str(settings.get(key) or "").strip()
        if not wanted:
            continue
        if not models:
            return wanted
        for candidate in models:
            if candidate == wanted or candidate.startswith(f"{wanted}:"):
                return candidate
    return models[0] if models else ""


def summarise_models(payload: dict[str, Any] | None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The inventory, plus which model the trainer would pick and whether it exists."""
    payload = payload or {}
    models = [item for item in (payload.get("models") or []) if isinstance(item, dict)]
    rows = []
    for item in models:
        rows.append(
            {
                "name": item.get("name"),
                "size_gb": round(float(item.get("size") or 0) / 1e9, 2),
                "context_length": item.get("context_length"),
                "capabilities": item.get("capabilities") or [],
                "complete": bool(item.get("complete")),
                "router_status": item.get("router_status"),
            }
        )
    rows.sort(key=lambda row: (-(row["context_length"] or 0), row["name"] or ""))
    names = [str(row["name"]) for row in rows if row["name"]]
    chosen = choose_model(settings, names)
    preferred = str((settings or {}).get("trainer_model") or "").strip()

    return {
        "total": len(rows),
        "models": rows,
        "chosen": chosen,
        "trainer_model_setting": preferred,
        "trainer_model_missing": bool(preferred) and preferred not in names,
        "largest_context": max((row["context_length"] or 0) for row in rows) if rows else 0,
    }


def readiness(system: dict[str, Any] | None, runtime: dict[str, Any] | None,
              slots: dict[str, Any] | None) -> dict[str, Any]:
    """May a training pass start now? Conservative, and it says why not."""
    system = system or {}
    runtime = runtime or {}
    slots = slots or {}
    reasons: list[str] = []

    if system.get("error"):
        return {"ready": False, "reasons": [str(system["error"])],
                "free_vram_mb": None, "loaded_models": [], "slots": slots}

    free = system.get("free_vram_mb")
    if isinstance(free, int) and free and free < MIN_FREE_VRAM_MB:
        reasons.append(f"only {free} MB of GPU memory is free")
    if int(runtime.get("queued_requests") or 0) > 0:
        reasons.append(f"{runtime['queued_requests']} request(s) are already queued")
    if slots and slots.get("status") not in (None, "ok", "ready") and slots.get("available") == 0:
        reasons.append(slots.get("message") or "llama-server reports no free slot")

    return {
        "ready": not reasons,
        "reasons": reasons,
        "free_vram_mb": free,
        "total_vram_mb": system.get("total_vram_mb"),
        "context_length": system.get("context_length"),
        "loaded_models": runtime.get("loaded_models") or [],
        "active_requests": runtime.get("active_requests", 0),
        "queued_requests": runtime.get("queued_requests", 0),
        "slots": slots,
    }


async def lora(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Loaded LoRA adapters: proof that a fine-tune is actually in play."""
    base = proxy_base(settings)
    if not base:
        return {"adapters": [], "error": "llm_local_url is not set"}
    try:
        answer = await get_json(f"{base}/admin/lora")
    except Exception as exc:
        return {"adapters": [], "error": str(exc)}
    adapters = [item for item in (answer.get("adapters") or []) if isinstance(item, dict)]
    return {
        "total": len(adapters),
        "adapters": [
            {"id": item.get("id"), "path": item.get("path"), "scale": item.get("scale")}
            for item in adapters
        ],
    }


async def probe(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Everything the Lab needs to know about the host, in one answer.

    Never raises: an unreachable host is reported as ``reachable: False`` with the
    reason, because "the trainer cannot run" is a diagnosis, not a crash.
    """
    base = proxy_base(settings)
    if not base:
        return {
            "reachable": False,
            "reasons": ["llm_local_url is not set, so there is no model host to probe"],
            "ready": False,
        }

    system_payload = runtime_payload = slots_payload = None
    errors: list[str] = []
    for name, path, store in (
        ("system", "/admin/system", "system"),
        ("runtime", "/admin/runtime", "runtime"),
        ("slots", "/admin/slots", "slots"),
    ):
        try:
            answer = await get_json(f"{base}{path}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if store == "system":
            system_payload = answer
        elif store == "runtime":
            runtime_payload = answer
        else:
            slots_payload = answer

    if system_payload is None and runtime_payload is None:
        return {
            "reachable": False,
            "base_url": base,
            "ready": False,
            "reasons": errors or ["the model host did not answer"],
        }

    system = clean_system(system_payload)
    runtime = clean_runtime(runtime_payload)
    slots = summarise_slots(slots_payload)
    verdict = readiness(system, runtime, slots)
    try:
        models_payload = await get_json(f"{base}/admin/models")
    except Exception:
        models_payload = {}

    return {
        "reachable": True,
        "base_url": base,
        "dashboard_url": dashboard_base() or None,
        "errors": errors,
        **verdict,
        "system": system,
        "runtime": runtime,
        "models": summarise_models(models_payload, settings),
        "lora": await lora(settings),
    }


def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _model_record(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    return payload


def _categories(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-category scores from either benchmark shape.

    The general suite writes ``category_<group>`` blocks with a 0-100 ``score``;
    the SharedLLM suite writes a ``tasks`` list carrying ``category`` and
    ``score``/``success`` per task. Both become the same map.
    """
    categories: dict[str, dict[str, Any]] = {}
    for key, value in record.items():
        if key.startswith("category_") and isinstance(value, dict):
            categories[key[len("category_"):]] = {
                "score": value.get("score"),
                "tests_run": value.get("tests_run"),
                "tests_passed": value.get("tests_passed"),
                "letter": value.get("letter"),
            }
    tasks = record.get("tasks")
    if isinstance(tasks, list) and tasks:
        per_category: dict[str, list[float]] = {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            category = str(task.get("category") or "unknown")
            score = task.get("score")
            if score is None:
                score = 100.0 if task.get("success") else 0.0
            try:
                per_category.setdefault(category, []).append(float(score))
            except (TypeError, ValueError):
                continue
        for category, scores in per_category.items():
            categories.setdefault(
                category,
                {
                    "score": round(sum(scores) / len(scores), 1),
                    "tests_run": len(scores),
                    "tests_passed": sum(1 for value in scores if value >= 60),
                    "letter": None,
                },
            )
    return categories


def benchmark_summary(*, limit: int = 20) -> dict[str, Any]:
    """Every per-model result Alpaca has written, newest first.

    Reads both suites from disk (``data/llm_benchmarks/models`` and
    ``data/shared_llm_benchmarks/models``). A missing directory is reported, not
    raised: a machine with no results yet is a normal, honest state.
    """
    rows: list[dict[str, Any]] = []
    directories = results_dirs()
    for suite, directory in directories.items():
        if not directory.is_dir():
            continue
        pattern = "general_*.json" if suite == "general" else "shared_*.json"
        for path in directory.glob(pattern):
            payload = _load_json(path)
            if not payload:
                continue
            record = _model_record(payload)
            rows.append(
                {
                    "suite": suite,
                    "model": payload.get("model") or record.get("model"),
                    "generated_at": payload.get("generated_at") or record.get("last_updated"),
                    "file": path.name,
                    "categories": _categories(record),
                }
            )
    rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    for row in rows:
        scores = [
            value["score"]
            for value in row["categories"].values()
            if isinstance(value.get("score"), (int, float))
        ]
        row["average_score"] = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "directories": {name: str(path) for name, path in directories.items()},
        "total": len(rows),
        "results": rows[: max(1, limit)],
    }


def weakest_categories(
    summary: dict[str, Any] | None, model: str, *, floor: float = WEAK_BENCHMARK_SCORE
) -> list[dict[str, Any]]:
    """The categories this model provably struggles with, worst first.

    Only the general suite is used: its per-category blocks are scored the same
    way every run. One unlucky test is noise, so the caller decides whether the
    numbers are worth a proposal (``tests_run`` is returned with each row).
    """
    if not summary or not model:
        return []
    wanted = {model, model.replace("--", ":"), model.removesuffix(":latest")}
    rows = [
        row
        for row in summary.get("results") or []
        if str(row.get("model") or "") in wanted and row.get("suite") == "general"
    ]
    if not rows:
        return []

    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        for category, stats in (row.get("categories") or {}).items():
            score = stats.get("score")
            if not isinstance(score, (int, float)):
                continue
            current = merged.get(category)
            if current is None or float(score) < float(current["score"]):
                merged[category] = {"category": category, **stats}

    weak = [item for item in merged.values() if float(item["score"]) < floor]
    weak.sort(key=lambda item: float(item["score"]))
    return weak
