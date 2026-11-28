"""The facilitator cycle: dream, evaluate, propose, remember, repair.

This is the loop the household actually runs. Each pass:

1. builds the catalogue (so the curriculum always matches the code);
2. dreams the memory (compact, merge, prune, validate) and reads the report;
3. runs a bounded slice of the curriculum on the local model;
4. merges what the dream and the failures say into one ranked backlog;
5. optionally writes the top proposals into RAG as low-confidence lessons, so
   the next mission carries them;
6. optionally repairs a workspace file it was told about, proving the fix with
   that workspace's own tests;
7. saves the backlog and the cycle summary next to the evaluation reports.

Every step is isolated: a RAG outage or an unloaded model becomes a recorded
error, never a failed cycle, because the next pass needs the rest of the data.
Two things are not isolated, because they would make the whole pass a lie: a data
directory that cannot be written (nothing would be kept), and a second cycle
starting while one runs (:class:`CycleBusy`; the manual route and the background
loop share one lock). Infrastructure failures are recorded, never turned into
lessons (see ``dream.INFRASTRUCTURE_KINDS``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from typing import Any

from services.gateway.trainer import alpaca, curriculum, runner, selffix
from services.gateway.trainer import catalog as catalog_mod
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import llm as llm_mod

log = logging.getLogger("gateway.trainer.facilitator")

#: Tags need at least this many capabilities before an empty screen is a gap.
UI_GAP_MIN_ROUTES = 10

#: Tags that are not screens: internal categories, or the admin/ops surface.
_NON_SCREEN_TAGS = {"tools", "admin", "telemetry", "memory", "identity"}


class CycleBusy(RuntimeError):
    """A cycle is already running in this gateway."""


#: One cycle at a time: the manual route and the background loop both take it.
_CYCLE_LOCK = asyncio.Lock()


def cycle_running() -> bool:
    return _CYCLE_LOCK.locked()


def backlog_path() -> Any:
    return runner.DATA_DIR / "backlog.json"


def validate_repair(repair: dict[str, Any] | None) -> dict[str, Any] | None:
    """The repair a cycle was asked for, checked before any work starts."""
    if not repair:
        return None
    workspace_id = str(repair.get("workspace_id") or "").strip()
    path = str(repair.get("path") or "").strip()
    if not workspace_id or not path:
        raise ValueError("repair needs both workspace_id and path")
    raw = repair.get("attempts", 2)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"repair.attempts must be a whole number, not {raw!r}")
    if not 1 <= raw <= selffix.MAX_ATTEMPTS:
        raise ValueError(f"repair.attempts must be between 1 and {selffix.MAX_ATTEMPTS}, not {raw}")
    targets = repair.get("test_targets") or []
    if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
        raise ValueError("repair.test_targets must be a list of paths")
    error_text = repair.get("error_text")
    return {
        "workspace_id": workspace_id,
        "path": path,
        "attempts": raw,
        "test_targets": targets,
        "error_text": str(error_text) if error_text else None,
    }


async def _settings() -> dict[str, Any]:
    """Identity's settings, or an empty mapping when Identity cannot be reached.

    ``llm_local_url`` is a GlobalSetting, so probing the host with the settings is
    what makes the hardware gate correct in production; a failed read must not
    stop a pass (it degrades to "no host known", not to a crash).
    """
    try:
        from services.gateway.orchestrator import get_llm_settings

        return await get_llm_settings()
    except Exception as exc:  # pragma: no cover - Identity down
        log.warning("[trainer] could not read settings for the host probe: %s", exc)
        return {}


def hardware_proposal(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    """A pass that could not start because the machine was busy is a proposal.

    The household's model comes first. Recording *why* the trainer stepped
    aside means the reason is visible in the backlog instead of being guessed at
    from an empty run.
    """
    if not readiness or readiness.get("ready"):
        return []
    reasons = [str(reason) for reason in readiness.get("reasons") or []]
    if not reasons:
        return []
    return [
        dream_mod.make_proposal(
            "hardware_busy",
            "The model host was busy: nothing was trained",
            "The pass stepped aside so the household kept the GPU: " + "; ".join(reasons),
            priority=5,
            evidence=reasons,
            action="Nothing to fix here; the next pass will run when the host is idle "
            "(see GET /api/trainer/hardware).",
        )
    ]


def benchmark_proposals(model: str, summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Categories Alpaca's own benchmark proves the model is weak at.

    Only categories with at least two graded tests count: one unlucky test is
    noise, and a proposal built on noise teaches the wrong lesson.
    """
    proposals: list[dict[str, Any]] = []
    for item in alpaca.weakest_categories(summary, model):
        if int(item.get("tests_run") or 0) < 2:
            continue
        proposals.append(
            dream_mod.make_proposal(
                "benchmark_gap",
                f"{model} scores {item['score']}/100 on {item['category']}",
                f"Alpaca's benchmark graded {item['tests_run']} {item['category']} task(s) and the "
                "model passed "
                f"{item.get('tests_passed', 0)}. Add curriculum cases in that bucket before "
                "raising the token budget.",
                priority=7,
                evidence=[f"{item['category']}: {item['score']} ({item.get('letter') or '-'})"],
                action=f"Add one {item['category']} lesson and a matching evaluation case.",
            )
        )
    return proposals


def ui_proposals(catalog: dict[str, Any], *, limit: int = 4) -> list[dict[str, Any]]:
    """Capabilities that exist but have no screen: a derivable UI to-do list.

    The catalogue tags both routes/tools and pages. A tag with plenty of
    capabilities and no page carrying it is a real gap -- the API can do it and
    the app gives nobody a door to it.
    """
    features = catalog.get("features") or []
    page_tags: Counter[str] = Counter()
    route_tags: Counter[str] = Counter()
    for feature in features:
        tags = feature.get("tags") or []
        if feature.get("kind") == "page":
            page_tags.update(tags)
        else:
            route_tags.update(tags)

    proposals: list[dict[str, Any]] = []
    for tag, count in route_tags.most_common():
        if tag in _NON_SCREEN_TAGS:
            continue
        if count < UI_GAP_MIN_ROUTES or page_tags.get(tag, 0) > 0:
            continue
        proposals.append(
            dream_mod.make_proposal(
                "ui_gap",
                f'Give "{tag}" a place in the app',
                f"{count} capabilities are tagged {tag}, but no screen carries that tag. "
                "Add a page or a Workspace shortcut so the family can reach them.",
                priority=2,
                evidence=[f"{tag}: {count} capabilities, 0 screens"],
                action=f"Add a {tag} entry point to the Workspace UI.",
            )
        )
        if len(proposals) >= limit:
            break
    return proposals


async def cycle(
    *,
    user: str = "default",
    buckets: list[str] | None = None,
    limit: int = 12,
    budget_s: float = 600.0,
    apply_lessons: bool = True,
    repair: dict[str, Any] | None = None,
    complete_fn: Any | None = None,
    hardware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One full facilitator pass; returns a summary and writes the backlog.

    Raises :class:`CycleBusy` when another cycle holds the lock, ``ValueError``
    for a malformed ``repair``, and ``runner.TrainerStorageError`` when the data
    directory cannot be written -- all before any work is done.
    """
    checked_repair = validate_repair(repair)
    if _CYCLE_LOCK.locked():
        raise CycleBusy("A trainer cycle is already running; wait for it to finish.")
    async with _CYCLE_LOCK:
        runner.ensure_data_dir()
        return await _cycle(
            user=user,
            buckets=buckets,
            limit=limit,
            budget_s=budget_s,
            apply_lessons=apply_lessons,
            repair=checked_repair,
            complete_fn=complete_fn,
            hardware=hardware,
        )


async def _cycle(
    *,
    user: str,
    buckets: list[str] | None,
    limit: int,
    budget_s: float,
    apply_lessons: bool,
    repair: dict[str, Any] | None,
    complete_fn: Any | None,
    hardware: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.time()
    summary: dict[str, Any] = {
        "status": "SUCCESS",
        "user": user,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "errors": [],
    }

    # Ask the host before spending it. Alpaca owns the GPU; when the household is
    # using it, dreaming (RAG only) still runs, but the evaluation steps aside.
    # ``hardware`` is injectable so a test (or a caller that already probed) does
    # not have to reach the machine again.
    if hardware is None:
        hardware = await alpaca.probe(await _settings())
    summary["hardware"] = {
        "reachable": hardware.get("reachable"),
        "ready": hardware.get("ready"),
        "reasons": hardware.get("reasons") or [],
        "free_vram_mb": hardware.get("free_vram_mb"),
        "model": (hardware.get("models") or {}).get("chosen"),
        "lora": (hardware.get("lora") or {}).get("total"),
    }

    catalog = catalog_mod.build_catalog()
    cases = curriculum.derive_curriculum(catalog, buckets=buckets, limit=limit)
    summary["catalog"] = {
        "fingerprint": catalog.get("fingerprint"),
        "features": catalog.get("counts", {}).get("total"),
    }
    summary["curriculum"] = curriculum.summarise(cases)

    dream_report: dict[str, Any] = {}
    try:
        dream_report = await dream_mod.dream(user)
    except Exception as exc:
        summary["errors"].append(f"dream: {exc}")
        log.warning("[trainer] dream failed: %s", exc)
    summary["dream"] = {
        "status": dream_report.get("status"),
        "reviewed": dream_report.get("reviewed"),
        "kept": dream_report.get("kept"),
        "compacted": len(dream_report.get("compacted") or []),
        "merged": len(dream_report.get("merged") or {}),
        "pruned": len(dream_report.get("pruned") or []),
        "degraded": len(dream_report.get("degraded") or []),
    }

    report: dict[str, Any] = {}
    # Unknown is not ready: an unreachable host, or one that could not report its
    # GPU, slots or requests, is not evaluated. "busy" (the host answered and said
    # not now) also files a hardware proposal; "unreachable" is only an error.
    reachable = hardware.get("reachable") is True
    ready = reachable and hardware.get("ready") is True
    busy = reachable and not ready
    if not ready:
        # Do not queue a run the household would feel.
        summary["eval"] = {
            "skipped": "hardware" if busy else "unreachable",
            "reasons": hardware.get("reasons") or [],
        }
        if not reachable:
            summary["errors"].append(
                "eval: the model host could not be probed: "
                + "; ".join(str(reason) for reason in hardware.get("reasons") or ["no answer"])
            )
    else:
        try:
            report = await runner.run_curriculum(
                cases,
                model=summary["hardware"].get("model") or None,
                complete_fn=complete_fn,
                budget_s=budget_s,
            )
        except llm_mod.LocalModelUnavailable as exc:
            summary["errors"].append(f"eval: {exc}")
            summary["eval"] = {"skipped": "model", "reasons": [str(exc)]}
        else:
            summary["eval"] = {
                "report_id": report["id"],
                "model": report["model"],
                "totals": report["totals"],
                "stopped_reason": report["stopped_reason"],
                "by_bucket": report["by_bucket"],
            }

    benchmarks: dict[str, Any] | None
    try:
        benchmarks = alpaca.benchmark_summary(limit=10)
    except alpaca.BenchmarksUnavailable as exc:
        benchmarks = None
        summary["errors"].append(f"benchmarks: {exc}")
        summary["benchmarks"] = {"error": str(exc)}
    else:
        summary["benchmarks"] = {
            "total": benchmarks.get("total"),
            "weak": alpaca.weakest_categories(benchmarks, summary["hardware"].get("model") or ""),
        }

    sim_ran = bool(
        report
        and report.get("cases")
        and report.get("stopped_reason") not in ("host_busy", "model_unavailable")
    )
    sim_history: dict[str, Any] = {}
    if sim_ran:
        try:
            sim_history = await dream_mod.sim_history(user)
        except dream_mod.RagError as exc:
            summary["errors"].append(f"sim: {exc}")
    summary["sim"] = {
        "problems": sim_history.get("count", 0),
        "retired": sum(1 for p in sim_history.get("problems") or [] if p.get("status") == "retired"),
    }

    proposals = dream_mod.merge_proposals(
        dream_mod.proposals_from_dream(dream_report),
        dream_mod.proposals_from_failures(report),
        dream_mod.proposals_from_sim(sim_history),
        hardware_proposal({"ready": False, "reasons": hardware.get("reasons")}) if busy else [],
        benchmark_proposals(summary["hardware"].get("model") or "", benchmarks),
        ui_proposals(catalog),
    )
    summary["proposals"] = proposals
    summary["lessons"] = []
    if apply_lessons:
        ingested = await dream_mod.ingest_proposals(proposals, user_id=user)
        summary["lessons"] = ingested["written"]
        summary["errors"].extend(
            f"lesson {item['proposal']}: {item['error']}" for item in ingested["failed"]
        )

    # Selector: record this run's per-case outcomes against the model's family,
    # then consolidate (portable winners, model-specific rules, retire repeats).
    if sim_ran:
        try:
            await dream_mod.record_eval_outcomes(report, user_id=user)
        except dream_mod.RagError as exc:
            summary["errors"].append(f"sim outcomes: {exc}")
        try:
            summary["sim_consolidation"] = await dream_mod.consolidate_sim()
        except dream_mod.RagError as exc:
            summary["errors"].append(f"sim consolidation: {exc}")

    if repair:
        try:
            result = await selffix.repair_workspace(
                repair["workspace_id"],
                path=repair["path"],
                error_text=repair["error_text"],
                test_targets=repair["test_targets"],
                attempts=repair["attempts"],
                user=user,
                complete_fn=complete_fn,
            )
        except llm_mod.LocalModelUnavailable as exc:
            summary["errors"].append(f"repair: {exc}")
        else:
            summary["repair"] = result.to_dict()

    backlog = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eval_report_id": report.get("id"),
        "hardware_ready": hardware.get("ready"),
        "model": summary["hardware"].get("model"),
        "proposals": proposals,
        "lessons": summary["lessons"],
    }
    _write_backlog(backlog)
    summary["backlog_path"] = str(backlog_path())
    summary["elapsed_s"] = round(time.time() - started, 2)
    return summary


def _write_backlog(backlog: dict[str, Any]) -> None:
    path = backlog_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(backlog, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise runner.TrainerStorageError(f"Could not write the backlog to {path}: {exc}") from exc


def read_backlog() -> dict[str, Any]:
    """The last saved backlog, or an empty one when no cycle has run yet.

    A backlog that exists but cannot be read raises: "no proposals" and "the file
    is corrupt" are different answers.
    """
    path = backlog_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"generated_at": None, "eval_report_id": None, "proposals": [], "lessons": []}
    except (OSError, ValueError) as exc:
        raise runner.TrainerStorageError(f"Could not read the backlog at {path}: {exc}") from exc
