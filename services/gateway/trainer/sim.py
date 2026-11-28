"""Gateway client for the two-tier Self-Improvement Memory.

Every harness reads a compact, bounded block before it plans and writes the
outcome afterwards. The ledger itself lives in RAG; this module is the one seam
tests replace and the only place that knows the RAG paths.

Failures are always soft: SIM is an optimization, never a precondition for
answering a person. A RAG outage yields an empty block and a debug log line.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.common.sim_memory import (
    HARNESS_CHAR_BUDGETS,
    OUTCOME_IMPROVED,
    OUTCOME_REGRESSED,
    STATUS_PORTABLE,
    format_sim_block,
    normalize_model_family,
)
from services.gateway.trainer import dream

log = logging.getLogger("gateway.trainer.sim")

#: The gateway-side guard, in addition to the formatter's own cap. A future
#: harness name must not silently receive an unbounded prompt block.
MAX_SIM_BLOCK_CHARS = max(HARNESS_CHAR_BUDGETS.values())


async def _call(method: str, path: str, **kwargs: Any) -> dict[str, Any] | None:
    """One RAG call, never raising. SIM is optional at runtime."""
    try:
        return await dream.request(method, path, timeout=8.0, **kwargs)
    except Exception as exc:  # RagError, timeout, malformed JSON: all optional
        log.debug("[sim] %s %s failed: %s", method, path, exc)
        return None


async def record(
    *,
    scope: str,
    user_id: str,
    harness: str,
    category: str,
    signature: str,
    rationale: str,
    edit_or_rule: str,
    predicted_outcome: str = "",
    observed_outcome: str = "",
    outcome_status: str = "pending",
    requires_admin: bool = False,
    tags: list[str] | None = None,
    model: str = "",
    model_family: str = "",
    domain: str = "",
    round: int = 0,
) -> dict[str, Any] | None:
    """Write one problem/attempt. System text is scrubbed by RAG."""
    return await _call(
        "POST",
        "/rag/sim/record",
        body={
            "scope": scope,
            "user_id": user_id,
            "harness": harness,
            "category": category,
            "signature": signature,
            "rationale": rationale,
            "edit_or_rule": edit_or_rule,
            "predicted_outcome": predicted_outcome,
            "observed_outcome": observed_outcome,
            "outcome_status": outcome_status,
            "requires_admin": requires_admin,
            "tags": list(tags or []),
            "model": model,
            "model_family": model_family or normalize_model_family(model),
            "domain": domain,
            "round": round,
        },
    )


async def query(
    *,
    user_id: str,
    harness: str,
    query: str,
    model: str,
    is_admin: bool = False,
    k: int = 8,
) -> list[dict[str, Any]]:
    """System SIM (admin-filtered) + exactly this user's User SIM."""
    data = await _call(
        "POST",
        "/rag/sim/query",
        body={
            "user_id": user_id,
            "harness": harness,
            "query": query,
            "model": model,
            "model_family": normalize_model_family(model),
            "is_admin": bool(is_admin),
            "k": k,
        },
    )
    results = (data or {}).get("results")
    return [item for item in results or [] if isinstance(item, dict)]


async def context_for_turn(
    *,
    user_id: str,
    harness: str,
    query: str,
    model: str,
    is_admin: bool = False,
    k: int = 6,
) -> tuple[str, list[dict[str, Any]]]:
    """The bounded prompt block and the rows injected (for outcome learning)."""
    items = await query(
        user_id=user_id, harness=harness, query=query, model=model,
        is_admin=is_admin, k=k,
    )
    block = format_sim_block(
        items,
        harness,
        normalize_model_family(model),
        is_admin=is_admin,
    )
    return block[:MAX_SIM_BLOCK_CHARS], items


async def record_outcome(
    *,
    problem_id: str,
    observed_outcome: str,
    outcome_status: str,
    model: str = "",
    harness: str = "",
    domain: str = "",
    round: int = 0,
    attempt_id: str | None = None,
) -> dict[str, Any] | None:
    return await _call(
        "POST",
        "/rag/sim/outcome",
        body={
            "problem_id": problem_id,
            "attempt_id": attempt_id,
            "observed_outcome": observed_outcome,
            "outcome_status": outcome_status,
            "model": model,
            "model_family": normalize_model_family(model),
            "harness": harness,
            "domain": domain,
            "round": round,
        },
    )


async def record_injected_outcomes(
    items: list[dict[str, Any]],
    *,
    success: bool,
    model: str,
    harness: str,
    observed_outcome: str = "",
) -> None:
    """Attribute this turn/mission's result to the SIM rules that were injected."""
    status = OUTCOME_IMPROVED if success else OUTCOME_REGRESSED
    for item in items:
        problem_id = str(item.get("problem_id") or "")
        if not problem_id:
            continue
        await record_outcome(
            problem_id=problem_id,
            observed_outcome=observed_outcome or ("the turn completed" if success else "the turn failed"),
            outcome_status=status,
            model=model,
            harness=harness,
        )


async def history(
    *, user_id: str, harness: str | None = None, include_retired: bool = True, limit: int = 500
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "user_id": user_id,
        "include_retired": str(include_retired).lower(),
        "limit": limit,
    }
    if harness:
        params["harness"] = harness
    return await _call("GET", "/rag/sim/history", params=params) or {"problems": [], "count": 0}


async def consolidate() -> dict[str, Any]:
    return await _call("POST", "/rag/sim/consolidate") or {"promoted": [], "retired": [], "model_specific": []}


_ERROR_HINT = re.compile(
    r"(?:error|exception|failed|invalid|unknown|not found|unrecognized|missing|required|ambiguous|did you mean)",
    re.IGNORECASE,
)


def looks_like_tool_error(result: str) -> bool:
    """Whether a tool result is a failure worth learning from.

    Deliberately conservative: a normal result that merely contains the word
    "error" in quoted content is uncommon, and a missed lesson is cheaper than
    polluting System SIM with false recoveries.
    """
    text = str(result or "").strip()
    if not text:
        return False
    if re.search(r"\b(?:HTTP\s*)?4\d\d\b|\b(?:HTTP\s*)?5\d\d\b", text):
        return True
    return bool(_ERROR_HINT.search(text))


def is_user_specific_error(error: str) -> bool:
    """Disambiguations/corrections belong to User SIM, not shared System SIM."""
    return bool(
        re.search(
            r"\b(ambiguous|did you mean|which one|clarify|disambiguat|confirmation|"
            r"which (?:person|room|device|file)|is that you|not sure who)\b",
            str(error or ""),
            re.IGNORECASE,
        )
    )


async def record_tool_recovery(
    *,
    user_id: str,
    harness: str,
    action: str,
    error: str,
    model: str,
    query: str = "",
) -> None:
    """A failed tool call recovered: keep the correction for the next time.

    Schema/alias/validation mistakes are shared System SIM (scrubbed); a
    user-specific disambiguation or correction stays in that user's SIM.
    """
    error = str(error or "").strip()
    if not error:
        return
    user_specific = is_user_specific_error(error)
    scope = "user" if user_specific else "system"
    owner = user_id if user_specific else "default"
    signature = f"{str(action or '').lower()}::{error.splitlines()[0][:160]}"
    rule = (
        f"When `{action}` returns `{error.splitlines()[0][:180]}`, change the argument "
        "shape or the named alias and retry once; do not resend the same call unchanged."
    )
    await record(
        scope=scope,
        user_id=owner,
        harness=harness,
        category="tool_recovery",
        signature=signature,
        rationale=f"The `{action}` tool rejected the first call, so the corrected call is the reusable part.",
        edit_or_rule=rule,
        predicted_outcome="The corrected call succeeds.",
        observed_outcome="The corrected call succeeded.",
        outcome_status=OUTCOME_IMPROVED,
        model=model,
        domain=str(query or "")[:200],
        tags=["tool-recovery", str(action or "").lower()],
    )


async def record_failure_lesson(
    *,
    user_id: str,
    harness: str,
    category: str,
    signature: str,
    rationale: str,
    rule: str,
    observed_outcome: str,
    model: str,
    scope: str = "system",
    outcome_status: str = OUTCOME_REGRESSED,
    domain: str = "",
) -> None:
    """Record a verified failure (a stall, a failed repair) as a negative lesson.

    Negative lessons are the most valuable part of the ledger: a proposer must
    see them so it does not propose the same failed fix again.
    """
    await record(
        scope=scope,
        user_id=user_id if scope == "user" else "default",
        harness=harness,
        category=category,
        signature=signature,
        rationale=rationale,
        edit_or_rule=rule,
        predicted_outcome="The fix would unblock the task.",
        observed_outcome=observed_outcome,
        outcome_status=outcome_status,
        model=model,
        domain=domain,
        tags=[category, "negative"],
    )


async def record_success_lesson(
    *,
    user_id: str,
    harness: str,
    category: str,
    signature: str,
    rationale: str,
    rule: str,
    observed_outcome: str,
    model: str,
    scope: str = "system",
    domain: str = "",
) -> None:
    await record(
        scope=scope,
        user_id=user_id if scope == "user" else "default",
        harness=harness,
        category=category,
        signature=signature,
        rationale=rationale,
        edit_or_rule=rule,
        predicted_outcome="The approach completes the task.",
        observed_outcome=observed_outcome,
        outcome_status=OUTCOME_IMPROVED,
        model=model,
        domain=domain,
        tags=[category],
    )


def portable_ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("problem_id")) for item in items if item.get("status") == STATUS_PORTABLE]
