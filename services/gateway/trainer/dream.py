"""RAG dreaming, and the improvement proposals a dream implies.

Dreaming already compacts, merges, prunes and validates Raven's lessons. What it
does not do is *say what to do next*. This module reads a dream report and the
evaluation report and turns them into ranked, actionable proposals -- rewrite
this degraded lesson, test that unvalidated one, document the tool the model
keeps missing, restart the model that stopped answering. Accepted proposals can
be written back into RAG as ``improvement_proposal`` lessons, which is how the
next dreaming pass sees them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import aiohttp

from services.common.http import get_client
from services.gateway.config import INTERNAL_SECRET, RAG_SVC

log = logging.getLogger("gateway.trainer.dream")

DEFAULT_TIMEOUT_S = 60.0

#: Never write more than this many proposals into memory in one cycle.
MAX_LESSONS_PER_CYCLE = 3


class RagError(RuntimeError):
    """A RAG call did not succeed (any non-2xx answer, or no answer at all)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RagUnavailable(RagError):
    """The RAG service did not answer, or answered with a 5xx."""


class RagRejected(RagError):
    """RAG answered 4xx: the request itself was refused (bad payload, auth)."""


#: Proposal kinds that describe the machine, not the model's knowledge. They are
#: shown in the backlog but never written into ``system_learnings``: a lesson
#: saying "the GPU was busy" would teach every later mission something false.
INFRASTRUCTURE_KINDS = frozenset({"hardware_busy", "host_busy", "model_unavailable"})
#: Advice about the SIM ledger itself (a retired fix) is shown to a person but is
#: not a behavioural lesson for the model.
NON_LESSON_KINDS = frozenset({"sim_retired"})


async def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Call the RAG service (the one seam tests replace).

    Only a 2xx is a result. A 4xx raises :class:`RagRejected` (it used to be
    returned as if it were data, so a refused lesson write counted as written),
    a 5xx or a connection failure raises :class:`RagUnavailable`.
    """
    try:
        # Reuse the pooled client instead of opening (and closing) a session per
        # SIM/RAG call; the short keep-alive means a dormant trainer does not
        # hold a dead connection.
        async with get_client().request(
            method,
            f"{RAG_SVC}{path}",
            params=params,
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise RagUnavailable(f"RAG {path} did not answer: {type(exc).__name__}: {exc}") from exc
    try:
        data: Any = json.loads(text) if text.strip() else {}
    except ValueError:
        data = {"message": text[:500]}
    if status >= 400:
        detail = data.get("detail") or data.get("message") if isinstance(data, dict) else data
        cls = RagUnavailable if status >= 500 else RagRejected
        raise cls(f"RAG {path} answered {status}: {str(detail)[:300]}", status=status)
    return data if isinstance(data, dict) else {"detail": data}


async def dream(user_id: str = "default", *, compact_at: int = 600, summary_len: int = 400) -> dict[str, Any]:
    """Run the consolidation passes and return the dream report."""
    return await request(
        "POST",
        "/rag/dream",
        params={"user_id": user_id, "compact_at": compact_at, "summary_len": summary_len},
        timeout=180.0,
    )


async def pending(user_id: str = "default") -> dict[str, Any]:
    """The lessons the next mission should validate (RSI self-test checklist)."""
    return await request("GET", "/rag/dream/pending", params={"user_id": user_id})


async def ingest_benchmark_results(models: list[str] | None = None) -> dict[str, Any]:
    """Fold Alpaca's measured benchmark results into lesson confidence.

    RAG owns the reading (``ALPACA_BENCHMARK_RESULTS_DIR``) and the scoring; this
    is the one call that makes the trainer's lesson priorities reflect what the
    model has actually proven on this machine.
    """
    return await request(
        "POST",
        "/rag/benchmark/ingest-results",
        body=list(models or []),
        timeout=180.0,
    )


async def validate(lesson_id: str, passed: bool, evidence: str, *, user_id: str = "default") -> dict[str, Any]:
    """Record that a lesson was tested, so dreaming can weight it later."""
    return await request(
        "POST",
        "/rag/dream/validate",
        params={"user_id": user_id},
        body={"lesson_id": lesson_id, "passed": passed, "evidence": evidence[:500]},
    )


async def sim_history(
    user_id: str = "default", *, harness: str | None = None, include_retired: bool = True
) -> dict[str, Any]:
    """The full SIM ledger, including retired/regressed attempts.

    Proposers read this before drafting anything so they understand why a rule
    exists and never re-propose a fix that already failed.
    """
    params: dict[str, Any] = {"user_id": user_id, "include_retired": str(include_retired).lower(), "limit": 500}
    if harness:
        params["harness"] = harness
    return await request("GET", "/rag/sim/history", params=params, timeout=30.0)


async def sim_record(
    *,
    scope: str,
    user_id: str,
    harness: str,
    category: str,
    signature: str,
    rationale: str,
    edit_or_rule: str,
    observed_outcome: str,
    outcome_status: str,
    model: str = "",
    domain: str = "",
) -> dict[str, Any]:
    """Record one SIM problem + attempt through the RAG ledger."""
    return await request(
        "POST",
        "/rag/sim/record",
        body={
            "scope": scope, "user_id": user_id, "harness": harness,
            "category": category, "signature": signature, "rationale": rationale,
            "edit_or_rule": edit_or_rule, "observed_outcome": observed_outcome,
            "outcome_status": outcome_status, "model": model, "domain": domain,
        },
        timeout=30.0,
    )


async def sim_outcome(
    problem_id: str,
    *,
    observed_outcome: str,
    outcome_status: str,
    model: str = "",
    harness: str = "",
) -> dict[str, Any]:
    """Selector hook: attribute an outcome to the active model family."""
    return await request(
        "POST",
        "/rag/sim/outcome",
        body={
            "problem_id": problem_id,
            "observed_outcome": observed_outcome[:500],
            "outcome_status": outcome_status,
            "model": model,
            "harness": harness,
        },
        timeout=30.0,
    )


async def consolidate_sim() -> dict[str, Any]:
    """Selector hook: promote portable winners, mark model-specific, retire regressions."""
    return await request("POST", "/rag/sim/consolidate", timeout=60.0)


async def record_eval_outcomes(report: dict[str, Any] | None, *, user_id: str = "default", limit: int = 20) -> int:
    """Record each evaluated case's result against the model that ran it.

    The selector half of the proposer-selector loop: a case that keeps failing
    becomes a negative lesson (so no proposer repeats it), and a case that now
    passes is evidence for the rule that was active. Best-effort per case.
    """
    if not report:
        return 0
    model = str(report.get("model") or "")
    written = 0
    for case in [c for c in report.get("cases") or [] if isinstance(c, dict)][: max(0, limit)]:
        if case.get("infrastructure_error"):
            continue
        case_id = str(case.get("id") or case.get("title") or "")[:180]
        if not case_id:
            continue
        passed = bool(case.get("passed"))
        title = str(case.get("title") or case_id)[:180]
        try:
            await sim_record(
                scope="system",
                user_id="default",
                harness="raven",
                category="eval_case",
                signature=case_id,
                rationale=f"The curriculum case `{title}` is used to tell whether a model has learned this.",
                edit_or_rule=(
                    f"For `{title}`, the verified approach is the one the case checks."
                    if passed
                    else f"For `{title}`, do not repeat the approach that failed this case."
                ),
                observed_outcome=str(case.get("error") or ("passed" if passed else "failed"))[:300],
                outcome_status="improved" if passed else "regressed",
                model=model,
            )
            written += 1
        except RagError as exc:
            log.debug("[trainer.dream] SIM case outcome skipped for %s: %s", case_id, exc)
    try:
        await consolidate_sim()
    except RagError as exc:
        log.debug("[trainer.dream] SIM consolidation skipped: %s", exc)
    return written


def _proposal_id(kind: str, detail: str) -> str:
    digest = hashlib.sha1(f"{kind}\x00{detail}".encode()).hexdigest()[:8]
    return f"proposal-{kind}-{digest}"


def _proposal(
    kind: str, title: str, detail: str, *, priority: int, evidence: list[str], action: str
) -> dict[str, Any]:
    return {
        "id": _proposal_id(kind, detail),
        "kind": kind,
        "title": title,
        "detail": detail,
        "priority": priority,
        "evidence": evidence[:8],
        "action": action,
        "infrastructure": kind in INFRASTRUCTURE_KINDS,
    }


#: Public alias: the facilitator builds its own kinds of proposal (UI gaps).
make_proposal = _proposal


def proposals_from_dream(report: dict[str, Any] | None, *, limit: int = 6) -> list[dict[str, Any]]:
    """What a dream report says the memory needs next."""
    if not report or str(report.get("status") or "").upper() != "SUCCESS":
        return []
    proposals: list[dict[str, Any]] = []

    for lesson_id in [str(item) for item in report.get("degraded") or []][:3]:
        proposals.append(
            _proposal(
                "lesson_rewrite",
                f"Rewrite degraded lesson {lesson_id}",
                f"Lesson {lesson_id} is missing a rule, root cause or outcome, so it cannot "
                "guide a mission. Rewrite it through SystemLearningRequest with all three.",
                priority=9,
                evidence=[lesson_id],
                action="Rewrite the lesson so it states a rule, a root cause and an outcome.",
            )
        )

    for item in (report.get("validation_checklist") or [])[:3]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if int(item.get("applied_count") or 0) <= 0:
            continue
        proposals.append(
            _proposal(
                "lesson_validation",
                f"Prove lesson {item['id']} still holds",
                f"Lesson {item['id']} has been applied {item['applied_count']} time(s) and never "
                f"checked: {str(item.get('rule') or '')[:160]}",
                priority=6,
                evidence=[str(item["id"])],
                action="Run one mission that exercises the rule, then report "
                "SystemLearningRequest action=validate.",
            )
        )

    reviewed = int(report.get("reviewed") or 0)
    if reviewed < 5:
        proposals.append(
            _proposal(
                "lesson_seed",
                "Seed the memory from the feature catalogue",
                f"Only {reviewed} lesson(s) exist. The catalogue knows every route, tool and "
                "screen, so its groups can become protocol lessons for the capabilities Raven "
                "keeps forgetting.",
                priority=4,
                evidence=[f"reviewed={reviewed}"],
                action="Ingest one lesson per capability group the evaluation fails on.",
            )
        )

    compacted = report.get("compacted") or []
    if isinstance(compacted, dict):
        compacted = list(compacted)
    if compacted:
        proposals.append(
            _proposal(
                "curriculum_tighten",
                "Keep lessons short enough for the mission prompt",
                f"{len(compacted)} lesson(s) were compacted this pass; long lessons crowd out "
                "the tools a small model needs in context.",
                priority=3,
                evidence=[str(item) for item in compacted][:5],
                action="Write new lessons with a one-sentence rule and a 400-character summary.",
            )
        )

    return sorted(proposals, key=lambda p: (-p["priority"], p["title"]))[:limit]


_TOOL_TITLE = re.compile(r"^Choose ([A-Za-z0-9_]+) for")


def proposals_from_failures(report: dict[str, Any] | None, *, limit: int = 6) -> list[dict[str, Any]]:
    """What a failed evaluation says to fix: a tool nobody documented, a bucket."""
    if not report:
        return []
    proposals: list[dict[str, Any]] = []
    cases = [case for case in report.get("cases") or [] if isinstance(case, dict)]

    last_error = next((str(case.get("error")) for case in reversed(cases) if case.get("error")), "")
    if report.get("stopped_reason") == "host_busy":
        proposals.append(
            _proposal(
                "host_busy",
                "The model host was busy: the run stopped",
                "The household kept the GPU (no free slot in time), so the run stopped "
                "early. Nothing about the model's knowledge was measured.",
                priority=5,
                evidence=[last_error] if last_error else [],
                action="Nothing to fix; run the evaluation again when the host is idle.",
            )
        )
    elif report.get("stopped_reason") == "model_unavailable":
        proposals.append(
            _proposal(
                "model_unavailable",
                "The local model stopped answering",
                "The run stopped because the model host failed repeatedly. Check that the "
                "trainer's model is installed and loaded before training again.",
                priority=10,
                evidence=[last_error] if last_error else [],
                action="Install or load the configured local model, then re-run the evaluation.",
            )
        )

    undocumented: dict[str, list[str]] = {}
    for case in cases:
        if case.get("passed") or case.get("source") != "catalog:tool_registry":
            continue
        if case.get("infrastructure_error"):
            continue
        match = _TOOL_TITLE.search(str(case.get("title") or ""))
        if match:
            undocumented.setdefault(match.group(1), []).append(str(case.get("id")))
    for tool, evidence in sorted(undocumented.items())[:3]:
        proposals.append(
            _proposal(
                "tool_doc",
                f"Teach the model when to use {tool}",
                f"The model did not pick {tool} from its own summary. Add one line to "
                "prompts/single_turn_tool_guide.md naming the situation, then re-run the case.",
                priority=8,
                evidence=evidence,
                action=f"Document {tool}'s trigger phrase in the single-turn tool guide.",
            )
        )

    teacher_failed = list(((report.get("by_bucket") or {}).get("teacher") or {}).get("failed_ids") or [])
    if teacher_failed:
        # A teacher case failed: a strong model solved this exact task, so the
        # worked example exists. The fix is to make sure Raven can retrieve it.
        proposals.append(
            _proposal(
                "teacher_gap",
                f"The local model missed {len(teacher_failed)} task(s) a teacher solved",
                "The teacher's verified steps for these tasks are on record. Write them as "
                "worked-example lessons so missions retrieve them, then re-run the teacher bucket.",
                priority=7,
                evidence=teacher_failed,
                action="Run POST /api/trainer/teacher/lessons, then evaluate the teacher bucket again.",
            )
        )

    for bucket, stats in (report.get("by_bucket") or {}).items():
        failed = list(stats.get("failed_ids") or [])
        if not failed:
            continue
        proposals.append(
            _proposal(
                f"curriculum_{bucket}",
                f"Strengthen the {bucket} curriculum",
                f"{len(failed)} of {stats.get('cases')} {bucket} case(s) failed. Add a protocol "
                "lesson for the specific mistake before raising the token budget.",
                priority=min(9, 3 + len(failed)),
                evidence=failed,
                action=f"Add one {bucket} lesson covering the failed checks, then re-run.",
            )
        )

    return sorted(proposals, key=lambda p: (-p["priority"], p["title"]))[:limit]


def proposals_from_sim(history: dict[str, Any] | None, *, limit: int = 6) -> list[dict[str, Any]]:
    """What the SIM ledger says to verify next, and which fixes to never retry.

    ``history`` is the full ledger (retired rows included). A retired rule is
    surfaced with its rationale and observed outcome so the proposer sees *why*
    an earlier edit is gone instead of proposing it again; a candidate with
    repeated attempts needs a real evaluation, not another rewrite.
    """
    if not history:
        return []
    problems = [p for p in history.get("problems") or [] if isinstance(p, dict)]
    proposals: list[dict[str, Any]] = []
    for problem in problems:
        status = str(problem.get("status") or "")
        pid = str(problem.get("problem_id") or "")
        rule = str(problem.get("edit_or_rule") or "")
        attempts = problem.get("attempts") or []
        if status == "retired":
            proposals.append(
                _proposal(
                    "sim_retired",
                    f"Never re-propose retired SIM fix {pid}",
                    f"This fix was tried and retired: {rule[:180]}. Observed: "
                    f"{str(problem.get('observed_outcome') or '')[:180]}. Do not propose it again; "
                    "change the approach or the root cause first.",
                    priority=7,
                    evidence=[pid],
                    action="Read the retired attempt history before drafting a replacement.",
                )
            )
        elif status == "candidate" and len(attempts) >= 2:
            proposals.append(
                _proposal(
                    "sim_validate",
                    f"Prove the recurring SIM rule {pid}",
                    f"The rule has {len(attempts)} recorded attempt(s) but is still unverified: "
                    f"{rule[:200]}",
                    priority=6,
                    evidence=[pid],
                    action="Run the situation the rule addresses and record improved/regressed.",
                )
            )
        elif status == "model_specific":
            proposals.append(
                _proposal(
                    "sim_portability",
                    f"Check the model-specific SIM rule {pid} on another family",
                    f"This rule helped {', '.join(problem.get('verified_models') or []) or 'one family'} "
                    f"and regressed on {', '.join(problem.get('regressed_models') or []) or 'another'}. "
                    "Do not inject it there; verify a narrower version if it should be shared.",
                    priority=5,
                    evidence=[pid],
                    action="Keep the rule family-scoped unless a narrower rule proves portable.",
                )
            )
    return sorted(proposals, key=lambda p: (-p["priority"], p["title"]))[:limit]


def merge_proposals(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-duplicate proposals by id, keeping the highest priority."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for proposal in group:
            current = merged.get(proposal["id"])
            if current is None or proposal["priority"] > current["priority"]:
                merged[proposal["id"]] = proposal
    return sorted(merged.values(), key=lambda p: (-p["priority"], p["title"]))


def lesson_payload(proposal: dict[str, Any], *, user_id: str = "default") -> dict[str, Any]:
    """An accepted proposal as a compact ``system_learnings`` document.

    Same shape the execution learning handler writes, so the mission prompt
    renderer and the dreaming passes treat it like any other lesson. Confidence is
    deliberately low (0.35): a proposal is a hypothesis until a run proves it.
    """
    rule = f"When improving Jarvis: {proposal['action']}"
    summary = str(proposal.get("detail") or "")[:400]
    lesson_id = "lesson-" + hashlib.sha1(
        (user_id or "default").encode("utf-8") + b"\x00" + rule.encode("utf-8")
    ).hexdigest()[:10]
    content = json.dumps(
        {
            "id": lesson_id,
            "topic": f"trainer improvement: {proposal['kind']}",
            "rule": rule,
            "root_cause": proposal["title"],
            "outcome": "proposed",
            "confidence": 0.35,
            "tags": ["trainer", "improvement", proposal["kind"]],
            "summary": summary,
        },
        ensure_ascii=False,
    )
    return {
        "user_id": user_id,
        "content": content,
        "collection_name": "system_learnings",
        "metadata": {
            "id": lesson_id,
            "topic": f"trainer improvement: {proposal['kind']}",
            "rule": rule,
            "root_cause": proposal["title"],
            "outcome": "proposed",
            "confidence": 0.35,
            "tags": ["trainer", "improvement", proposal["kind"]],
            "type": "improvement_proposal",
            "supersedes": [],
            "source_proposal": proposal["id"],
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def is_lesson_worthy(proposal: dict[str, Any]) -> bool:
    """Only a proposal about the model's knowledge may become a lesson."""
    return (
        not proposal.get("infrastructure")
        and proposal.get("kind") not in INFRASTRUCTURE_KINDS
        and proposal.get("kind") not in NON_LESSON_KINDS
    )


async def ingest_proposals(
    proposals: list[dict[str, Any]],
    *,
    user_id: str = "default",
    limit: int = MAX_LESSONS_PER_CYCLE,
) -> dict[str, Any]:
    """Write the top lesson-worthy proposals into RAG.

    Infrastructure proposals (a busy or broken host) are never written: they are
    facts about the machine on one afternoon, not rules for the model. Returns
    ``{"written": [lesson ids], "failed": [{"proposal", "error"}]}``: a refused
    write (any RAG 4xx or 5xx) is reported to the caller, not counted as written.
    """
    written: list[str] = []
    failed: list[dict[str, str]] = []
    worthy = [proposal for proposal in proposals if is_lesson_worthy(proposal)]
    for proposal in worthy[: max(0, limit)]:
        payload = lesson_payload(proposal, user_id=user_id)
        try:
            await request("POST", "/rag/ingest", body=payload, timeout=30.0)
        except RagError as exc:
            log.warning("[trainer.dream] could not ingest %s: %s", proposal["id"], exc)
            failed.append({"proposal": str(proposal["id"]), "error": str(exc)})
            continue
        written.append(str(payload["metadata"]["id"]))
    return {"written": written, "failed": failed}
