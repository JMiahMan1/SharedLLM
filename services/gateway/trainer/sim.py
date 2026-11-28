"""Gateway client for the two-tier Self-Improvement Memory.

Every harness reads a compact, bounded block before it plans and writes the
outcome afterwards. The ledger itself lives in RAG; this module is the one seam
tests replace and the only place that knows the RAG paths.

Failures are always soft: SIM is an optimization, never a precondition for
answering a person. A RAG outage yields an empty block and a debug log line.
"""
from __future__ import annotations

import json
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


#: The ``query`` parameter of :func:`context_for_turn` shadows the function of
#: the same name; call through this alias so retrieval actually happens.
_retrieve = query


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
    items = await _retrieve(
        user_id=user_id, harness=harness, query=query, model=model,
        is_admin=is_admin, k=k,
    )
    block = format_sim_block(
        items,
        harness,
        normalize_model_family(model),
        is_admin=is_admin,
        preserve_order=True,
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


#: An error that a person or tool wrote at the start of the result. Anchoring is
#: the point: "Fixed the failing test" and a file that merely mentions an error
#: in its body are normal results, not failed calls.
_ERROR_PREFIX = re.compile(
    r"^(?:"
    r"error\b|failed\b|failure\b|exception\b|validationerror\b|traceback\b|"
    r"sorry,?\s+(?:i|we)\s+could(?:n'?t| not)\b|"
    r"i\s+could(?:n'?t| not)\b|unable\s+to\b|cannot\b|could\s+not\b"
    r")",
    re.IGNORECASE,
)
#: ``HTTP 404`` / ``HTTP/1.1 503`` is a real status, not the number 404 in prose.
_HTTP_STATUS = re.compile(r"\bHTTP(?:/\d(?:\.\d)?)?\s+\d{3}\b", re.IGNORECASE)
_STRUCTURED_ERROR_STATUS = frozenset(
    {"error", "failed", "failure", "timeout", "rejected", "denied"}
)


def _structured_error(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    status = str(data.get("status") or data.get("state") or "").strip().lower()
    if status in _STRUCTURED_ERROR_STATUS:
        return True
    for key in ("status_code", "statusCode", "code"):
        try:
            if int(data.get(key)) >= 400:
                return True
        except (TypeError, ValueError):
            continue
    if data.get("success") is False or data.get("ok") is False:
        return True
    # ``error``/``errors`` is only a failure when it carries something. A tool
    # returning a normal record may legitimately include an empty error field.
    return bool(data.get("error") or data.get("errors"))


def looks_like_tool_error(result: str) -> bool:
    """Whether a tool result is a failure worth learning from.

    Prose is not an error signal. Only a structured failure, a leading error
    prefix, or an explicit HTTP error status counts; "Fixed the failing test"
    and a search hit that discusses errors stay normal results.
    """
    text = str(result or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and _structured_error(parsed):
        return True
    if _ERROR_PREFIX.match(text):
        return True
    return bool(_HTTP_STATUS.search(text))


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


def _as_mapping(value: dict | str | None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _value_shape(value: Any) -> str:
    """A value's type/shape, never the value itself.

    System SIM is shared across users, so an argument diff may name keys and
    shapes but must never copy a token, path or personal string into the ledger.
    """
    if isinstance(value, dict):
        return f"object({len(value)} keys)"
    if isinstance(value, (list, tuple)):
        return f"list({len(value)})"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return f"string({len(str(value))})"


def describe_arg_difference(
    failed_args: dict | str | None, working_args: dict | str | None
) -> str:
    """Concrete key/shape difference between a rejected and a working call."""
    failed = _as_mapping(failed_args)
    working = _as_mapping(working_args)
    if failed is None or working is None:
        return ""
    removed = sorted(set(failed) - set(working))
    added = sorted(set(working) - set(failed))
    changed: list[str] = []
    for key in sorted(set(failed) & set(working)):
        before, after = failed[key], working[key]
        if _value_shape(before) != _value_shape(after):
            changed.append(f"{key} ({_value_shape(before)} -> {_value_shape(after)})")
        elif before != after:
            changed.append(f"{key} (value changed)")
    parts: list[str] = []
    if removed:
        parts.append("removed " + ", ".join(removed))
    if added:
        parts.append("added " + ", ".join(added))
    if changed:
        parts.append("changed " + "; ".join(changed))
    return "; ".join(parts)[:400]


async def record_tool_recovery(
    *,
    user_id: str,
    harness: str,
    action: str,
    error: str,
    model: str,
    query: str = "",
    failed_args: dict | str | None = None,
    working_args: dict | str | None = None,
) -> None:
    """A failed tool call recovered: keep the correction for the next time.

    Schema/alias/validation mistakes are shared System SIM (scrubbed); a
    user-specific disambiguation or correction stays in that user's SIM. When
    the caller supplies both payloads, the lesson names the concrete argument
    keys/shapes that changed instead of repeating generic advice.
    """
    error = str(error or "").strip()
    if not error:
        return
    user_specific = is_user_specific_error(error)
    scope = "user" if user_specific else "system"
    owner = user_id if user_specific else "default"
    signature = f"{str(action or '').lower()}::{error.splitlines()[0][:160]}"
    difference = describe_arg_difference(failed_args, working_args)
    first_line = error.splitlines()[0][:180]
    if difference:
        rule = (
            f"When `{action}` rejects a call, adjust the arguments ({difference}) "
            "and retry once; do not resend the same arguments unchanged."
        )
        rationale = (
            f"The `{action}` tool rejected the first call with `{first_line}`; "
            f"the working call differed by {difference}."
        )
    else:
        rule = (
            f"When `{action}` returns `{first_line}`, change the argument "
            "shape or the named alias and retry once; do not resend the same call unchanged."
        )
        rationale = f"The `{action}` tool rejected the first call, so the corrected call is the reusable part."
    await record(
        scope=scope,
        user_id=owner,
        harness=harness,
        category="tool_recovery",
        signature=signature,
        rationale=rationale,
        edit_or_rule=rule,
        predicted_outcome="The corrected call succeeds.",
        observed_outcome="The corrected call succeeded.",
        outcome_status=OUTCOME_IMPROVED,
        model=model,
        domain=str(query or "")[:200],
        tags=["tool-recovery", str(action or "").lower()],
    )


#: Explicit, short corrections. A long question that merely says "no" is not a
#: correction; the user has to name what to do differently.
_CORRECTION_PATTERNS = (
    re.compile(
        r"^\s*(?:no|nope|nah|actually|wait|sorry)[,.!:\s]+"
        r"(?:i\s+(?:meant|said|wanted)|use|call|try|do|it'?s)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bi\s+(?:meant|said)\b", re.IGNORECASE),
    re.compile(r"\bactually,?\s+(?:use|call|try|do|it'?s)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(?:call|use)\b.{0,120}\b(?:call|use)\b", re.IGNORECASE),
    re.compile(r"\b(?:use|call|try)\b.{0,40}\binstead\b", re.IGNORECASE),
)
_PERSONAL_USER_IDS = frozenset({"", "default", "household", "anonymous"})


def detect_user_correction(query: str, history: list[dict[str, Any]] | None) -> dict[str, str] | None:
    """Detect an explicit, short user correction against the previous turn.

    Only the person's own wording is used. Returning ``None`` on anything
    ambiguous keeps ordinary questions out of User SIM.
    """
    text = str(query or "").strip()
    if not text or len(text) > 300:
        return None
    previous = ""
    for message in reversed(list(history or [])):
        if not isinstance(message, dict) or str(message.get("role", "")).lower() != "user":
            continue
        candidate = str(message.get("content", "")).strip()
        if candidate and candidate != text:
            previous = candidate
            break
    if not previous:
        return None
    if not any(pattern.search(text) for pattern in _CORRECTION_PATTERNS):
        return None
    return {
        "correction": text[:280],
        "previous": previous[:280],
    }


async def record_user_correction(
    *,
    user_id: str,
    harness: str,
    query: str,
    history: list[dict[str, Any]] | None,
    model: str = "",
) -> dict[str, Any] | None:
    """Persist a detected correction in that caller's User SIM, never System SIM."""
    owner = str(user_id or "").strip().lower()
    if owner in _PERSONAL_USER_IDS:
        return None
    detected = detect_user_correction(query, history)
    if detected is None:
        return None
    correction, previous = detected["correction"], detected["previous"]
    return await record(
        scope="user",
        user_id=owner,
        harness=harness,
        category="user_correction",
        signature=f"user-correction::{previous[:120]}::{correction[:120]}",
        rationale=f"The user corrected an earlier instruction. Earlier: {previous}",
        edit_or_rule=f"Follow this user's explicit correction: {correction}",
        predicted_outcome="The corrected interpretation matches what the user asked for.",
        observed_outcome="The user stated the correction explicitly.",
        outcome_status="pending",
        model=model,
        domain=str(query or "")[:200],
        tags=["user-correction"],
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
