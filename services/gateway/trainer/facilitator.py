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
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from typing import Any

from services.gateway.trainer import catalog as catalog_mod
from services.gateway.trainer import curriculum, runner, selffix
from services.gateway.trainer import dream as dream_mod

log = logging.getLogger("gateway.trainer.facilitator")

#: Tags need at least this many capabilities before an empty screen is a gap.
UI_GAP_MIN_ROUTES = 10

#: Tags that are not screens: internal categories, or the admin/ops surface.
_NON_SCREEN_TAGS = {"tools", "admin", "telemetry", "memory", "identity"}


def backlog_path() -> Any:
    return runner.DATA_DIR / "backlog.json"


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
) -> dict[str, Any]:
    """One full facilitator pass; returns a summary and writes the backlog."""
    started = time.time()
    summary: dict[str, Any] = {
        "status": "SUCCESS",
        "user": user,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "errors": [],
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

    report = await runner.run_curriculum(cases, complete_fn=complete_fn, budget_s=budget_s)
    summary["eval"] = {
        "report_id": report["id"],
        "model": report["model"],
        "totals": report["totals"],
        "stopped_reason": report["stopped_reason"],
        "by_bucket": report["by_bucket"],
    }

    proposals = dream_mod.merge_proposals(
        dream_mod.proposals_from_dream(dream_report),
        dream_mod.proposals_from_failures(report),
        ui_proposals(catalog),
    )
    summary["proposals"] = proposals
    summary["lessons"] = (
        await dream_mod.ingest_proposals(proposals, user_id=user) if apply_lessons else []
    )

    if repair and repair.get("workspace_id") and repair.get("path"):
        result = await selffix.repair_workspace(
            str(repair["workspace_id"]),
            path=str(repair["path"]),
            error_text=repair.get("error_text"),
            test_targets=repair.get("test_targets"),
            attempts=int(repair.get("attempts") or 2),
            user=user,
            complete_fn=complete_fn,
        )
        summary["repair"] = result.to_dict()

    backlog = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eval_report_id": report["id"],
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
    except OSError as exc:  # a read-only mount must not break the cycle
        log.warning("[trainer] could not write the backlog: %s", exc)


def read_backlog() -> dict[str, Any]:
    """The last saved backlog, or an empty one."""
    path = backlog_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generated_at": None, "eval_report_id": None, "proposals": [], "lessons": []}
