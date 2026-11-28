"""Persistence and hybrid retrieval for the two-tier Self-Improvement Memory.

The pure policy (scopes, scrubbing, model-family normalization, lifecycle and
the bounded prompt block) lives in :mod:`services.common.sim_memory`. This
module adds the SQLite ledger and the retrieval that always merges shared
System SIM with exactly one caller's User SIM.

Retrieval is "hybrid" in the practical sense:

* lexical token overlap over the rule and rationale,
* tag and harness affinity,
* optional embedding cosine similarity when an embedder is available.

Embedding failures are swallowed and the lexical/tag score is used alone, so a
missing model never makes the ledger unreadable.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from services.common.sim_memory import (
    OUTCOME_IMPROVED,
    OUTCOME_PENDING,
    OUTCOME_REGRESSED,
    STATUS_CANDIDATE,
    STATUS_MODEL_SPECIFIC,
    STATUS_PORTABLE,
    STATUS_RETIRED,
    STATUS_VERIFIED,
    SYSTEM_SCOPE,
    SYSTEM_USER,
    USER_SCOPE,
    is_regressed_for_family,
    is_verified_for_family,
    make_problem_id,
    normalize_harness,
    normalize_model_family,
    normalize_outcome_status,
    normalize_scope,
    scrub_system_sim_text,
    stable_signature,
    status_priority,
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the SIM tables (idempotent; safe on an existing database)."""
    from services.rag.db import create_sim_tables

    create_sim_tables(conn.cursor())
    conn.commit()


def _now() -> int:
    return int(time.time())


def _json_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return [str(v).strip().lower() for v in loaded if str(v).strip()] if isinstance(loaded, list) else []


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("tags", "verified_models", "regressed_models"):
        item[key] = _json_list(item.get(key))
    item["requires_admin"] = bool(item.get("requires_admin"))
    return item


def _attempt_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def record(
    conn: sqlite3.Connection,
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
    outcome_status: str = OUTCOME_PENDING,
    requires_admin: bool = False,
    tags: Sequence[str] | None = None,
    model: str = "",
    model_family: str = "",
    domain: str = "",
    round: int = 0,
) -> dict[str, Any]:
    """Record (or regroup) a problem and append an attempt.

    The same scope/user/harness/category/signature maps to one stable
    ``problem_id``; a repeat updates the rule and appends an attempt rather than
    creating a second, competing lesson. System text is scrubbed before storage.
    """
    scope = normalize_scope(scope)
    harness = normalize_harness(harness)
    outcome_status = normalize_outcome_status(outcome_status)
    owner = SYSTEM_USER if scope == SYSTEM_SCOPE else str(user_id or "").strip().lower()
    if not owner:
        raise ValueError("a user-scoped SIM problem needs a user_id")
    category = str(category or "general").strip().lower()
    signature = stable_signature(signature or edit_or_rule)
    if not signature:
        raise ValueError("a SIM problem needs a signature or an edit_or_rule")
    if scope == SYSTEM_SCOPE:
        rationale = scrub_system_sim_text(rationale)
        edit_or_rule = scrub_system_sim_text(edit_or_rule)
        predicted_outcome = scrub_system_sim_text(predicted_outcome)
        observed_outcome = scrub_system_sim_text(observed_outcome)
    if not str(edit_or_rule or "").strip():
        raise ValueError("a SIM problem needs an actionable edit_or_rule")
    problem_id = make_problem_id(
        scope=scope,
        user_id=owner,
        harness=harness,
        category=category,
        signature=signature,
    )
    family = normalize_model_family(model_family or model)
    now = _now()
    clean_tags = [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]
    existing = conn.execute(
        "SELECT * FROM sim_problems WHERE problem_id = ?", [problem_id]
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO sim_problems(
                problem_id, scope, user_id, harness, category, signature,
                rationale, edit_or_rule, predicted_outcome, observed_outcome,
                outcome_status, status, requires_admin, tags, verified_models,
                regressed_models, model, model_family, domain, round,
                retrieval_count, created_at, updated_at, last_used_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                problem_id, scope, owner, harness, category, signature,
                rationale, edit_or_rule, predicted_outcome, observed_outcome,
                outcome_status, STATUS_CANDIDATE, int(bool(requires_admin)),
                _json_dump(clean_tags), _json_dump([]), _json_dump([]),
                str(model or ""), family, str(domain or ""), int(round or 0),
                0, now, now, None,
            ],
        )
    else:
        # A retired rule stays retired when re-recorded, so a proposer cannot
        # accidentally revive a fix that already failed. An explicit improved
        # outcome below is the only path that can move it forward again.
        conn.execute(
            """
            UPDATE sim_problems SET
                rationale = ?, edit_or_rule = ?, predicted_outcome = ?,
                observed_outcome = CASE WHEN ? != '' THEN ? ELSE observed_outcome END,
                outcome_status = CASE WHEN ? != 'pending' THEN ? ELSE outcome_status END,
                requires_admin = MAX(requires_admin, ?),
                tags = ?, model = CASE WHEN ? != '' THEN ? ELSE model END,
                model_family = CASE WHEN ? != 'unknown' THEN ? ELSE model_family END,
                domain = CASE WHEN ? != '' THEN ? ELSE domain END,
                round = CASE WHEN ? > 0 THEN ? ELSE round END,
                updated_at = ?
            WHERE problem_id = ?
            """,
            [
                rationale, edit_or_rule, predicted_outcome,
                observed_outcome, observed_outcome,
                outcome_status, outcome_status,
                int(bool(requires_admin)),
                _json_dump(clean_tags), str(model or ""), str(model or ""),
                family, family,
                str(domain or ""), str(domain or ""),
                int(round or 0), int(round or 0),
                now, problem_id,
            ],
        )
    attempt_id = "sim-attempt-" + uuid.uuid4().hex[:16]
    conn.execute(
        """
        INSERT INTO sim_attempts(
            attempt_id, problem_id, harness, model, model_family, domain, round,
            rationale, edit_or_rule, predicted_outcome, observed_outcome,
            outcome_status, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            attempt_id, problem_id, harness, str(model or ""), family,
            str(domain or ""), int(round or 0), rationale, edit_or_rule,
            predicted_outcome, observed_outcome, outcome_status, now, now,
        ],
    )
    if outcome_status != OUTCOME_PENDING:
        record_outcome(
            conn,
            problem_id=problem_id,
            attempt_id=attempt_id,
            observed_outcome=observed_outcome,
            outcome_status=outcome_status,
            model=model,
            model_family=family,
        )
    conn.commit()
    problem = get_problem(conn, problem_id)
    assert problem is not None
    problem["attempt_id"] = attempt_id
    return problem


def get_problem(conn: sqlite3.Connection, problem_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sim_problems WHERE problem_id = ?", [problem_id]).fetchone()
    return _row_to_dict(row)


def _derive_status(verified: list[str], regressed: list[str]) -> str:
    """The lifecycle transition implied by the accumulated family outcomes."""
    verified = list(dict.fromkeys(verified))
    regressed = list(dict.fromkeys(regressed))
    judged = len(set(verified) | set(regressed))
    regression_rate = len(regressed) / judged if judged else 0.0
    if not verified and len(regressed) >= 2:
        return STATUS_RETIRED
    if len(verified) >= 2 and regression_rate <= 0.5:
        return STATUS_PORTABLE
    if verified and regressed:
        return STATUS_MODEL_SPECIFIC
    if verified:
        return STATUS_VERIFIED
    if len(regressed) >= 2:
        return STATUS_RETIRED
    return STATUS_CANDIDATE


def record_outcome(
    conn: sqlite3.Connection,
    *,
    observed_outcome: str,
    outcome_status: str,
    problem_id: str | None = None,
    attempt_id: str | None = None,
    model: str = "",
    model_family: str = "",
    harness: str = "",
    domain: str = "",
    round: int = 0,
) -> dict[str, Any]:
    """Record what actually happened and advance the model-family lifecycle."""
    if not problem_id and not attempt_id:
        raise ValueError("record_outcome needs a problem_id or an attempt_id")
    if not problem_id:
        row = conn.execute(
            "SELECT problem_id FROM sim_attempts WHERE attempt_id = ?", [attempt_id]
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown SIM attempt {attempt_id}")
        problem_id = str(row["problem_id"])
    problem = get_problem(conn, problem_id)
    if problem is None:
        raise KeyError(f"unknown SIM problem {problem_id}")
    outcome_status = normalize_outcome_status(outcome_status)
    if outcome_status == OUTCOME_PENDING:
        raise ValueError("record_outcome needs a final outcome_status")
    family = normalize_model_family(model_family or model)
    now = _now()
    if attempt_id:
        conn.execute(
            """
            UPDATE sim_attempts SET observed_outcome = ?, outcome_status = ?,
                model = CASE WHEN ? != '' THEN ? ELSE model END,
                model_family = CASE WHEN ? != 'unknown' THEN ? ELSE model_family END,
                harness = CASE WHEN ? != '' THEN ? ELSE harness END,
                domain = CASE WHEN ? != '' THEN ? ELSE domain END,
                round = CASE WHEN ? > 0 THEN ? ELSE round END,
                updated_at = ?
            WHERE attempt_id = ?
            """,
            [
                str(observed_outcome or ""), outcome_status,
                str(model or ""), str(model or ""),
                family, family,
                str(harness or ""), str(harness or ""),
                str(domain or ""), str(domain or ""),
                int(round or 0), int(round or 0),
                now, attempt_id,
            ],
        )
    verified = _json_list(problem.get("verified_models"))
    regressed = _json_list(problem.get("regressed_models"))
    if family != "unknown":
        if outcome_status == OUTCOME_IMPROVED:
            if family not in verified:
                verified.append(family)
            regressed = [f for f in regressed if f != family]
        elif outcome_status == OUTCOME_REGRESSED:
            if family not in regressed:
                regressed.append(family)
            verified = [f for f in verified if f != family]
    status = _derive_status(verified, regressed)
    conn.execute(
        """
        UPDATE sim_problems SET observed_outcome = ?, outcome_status = ?,
            verified_models = ?, regressed_models = ?, status = ?,
            model = CASE WHEN ? != '' THEN ? ELSE model END,
            model_family = CASE WHEN ? != 'unknown' THEN ? ELSE model_family END,
            updated_at = ?
        WHERE problem_id = ?
        """,
        [
            str(observed_outcome or ""), outcome_status,
            _json_dump(verified), _json_dump(regressed), status,
            str(model or ""), str(model or ""),
            family, family,
            now, problem_id,
        ],
    )
    conn.commit()
    updated = get_problem(conn, problem_id)
    assert updated is not None
    return updated


def list_problems(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    user_id: str | None = None,
    harness: str | None = None,
    status: str | None = None,
    include_retired: bool = False,
    category: str | None = None,
    is_admin: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Inspect the ledger. System SIM is shared; User SIM is always exact-owner."""
    clauses: list[str] = []
    params: list[Any] = []
    if scope:
        scope = normalize_scope(scope)
        clauses.append("scope = ?")
        params.append(scope)
        if scope == USER_SCOPE:
            owner = str(user_id or "").strip().lower()
            if not owner:
                raise ValueError("listing user-scoped SIM needs a user_id")
            clauses.append("user_id = ?")
            params.append(owner)
        else:
            clauses.append("user_id = ?")
            params.append(SYSTEM_USER)
            if not is_admin:
                clauses.append("requires_admin = 0")
    elif user_id:
        # A caller without an explicit scope sees their own User SIM plus
        # non-admin System SIM.
        clauses.append("((scope = 'system' AND requires_admin = 0) OR (scope = 'user' AND user_id = ?))")
        params.append(str(user_id).strip().lower())
    if harness:
        clauses.append("harness = ?")
        params.append(normalize_harness(harness))
    if status:
        clauses.append("status = ?")
        params.append(str(status).strip().lower())
    elif not include_retired:
        clauses.append("status != ?")
        params.append(STATUS_RETIRED)
    if category:
        clauses.append("category = ?")
        params.append(str(category).strip().lower())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM sim_problems {where} ORDER BY updated_at DESC, problem_id LIMIT ?",
        [*params, max(1, min(int(limit), 1000))],
    ).fetchall()
    return [item for row in rows if (item := _row_to_dict(row)) is not None]


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def query(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    harness: str,
    query: str = "",
    model: str = "",
    model_family: str = "",
    is_admin: bool = False,
    k: int = 8,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[dict[str, Any]]:
    """Hybrid retrieval over System SIM + exactly this caller's User SIM.

    System SIM is filtered by ``requires_admin``; User SIM is filtered to the
    exact, lower-cased ``user_id``. Retired lessons and lessons that regressed
    on the current model family never enter a live prompt.
    """
    harness = normalize_harness(harness)
    owner = str(user_id or "").strip().lower()
    if not owner:
        raise ValueError("SIM query needs a user_id")
    family = normalize_model_family(model_family or model)
    rows = conn.execute(
        """
        SELECT * FROM sim_problems
        WHERE (scope = 'system' AND (? = 1 OR requires_admin = 0))
           OR (scope = 'user' AND user_id = ?)
        """,
        [int(bool(is_admin)), owner],
    ).fetchall()
    items = [item for row in rows if (item := _row_to_dict(row)) is not None]
    q_tokens = _tokens(query)
    if embed_fn is not None and query.strip() and items:
        try:
            texts = [query] + [f"{item.get('edit_or_rule', '')} {item.get('rationale', '')}" for item in items]
            vectors = embed_fn(texts)
            if len(vectors) == len(texts):
                q_vec = vectors[0]
                for item, vec in zip(items, vectors[1:], strict=False):
                    item["_embedding"] = max(0.0, _cosine(q_vec, vec))
        except Exception:
            # Embeddings are an optional signal; lexical/tag retrieval is enough.
            pass
    scored: list[tuple[tuple[float, float, int], dict[str, Any]]] = []
    for item in items:
        status = str(item.get("status") or STATUS_CANDIDATE).lower()
        if status == STATUS_RETIRED:
            continue
        if bool(item.get("requires_admin")) and not is_admin:
            continue
        if status == STATUS_MODEL_SPECIFIC and not is_verified_for_family(item, family):
            continue
        if is_regressed_for_family(item, family):
            continue
        item_tokens = _tokens(f"{item.get('edit_or_rule', '')} {item.get('rationale', '')} {item.get('signature', '')}")
        overlap = len(q_tokens & item_tokens) if q_tokens else 0
        tag_hits = sum(1 for tag in item.get("tags") or [] if tag.lower() in q_tokens)
        same_harness = 1 if item.get("harness") == harness else 0
        # Cross-harness System SIM is allowed only when it actually matches the
        # question; a workspace-only rule must not crowd a Raven mission.
        if q_tokens and not overlap and not tag_hits and not same_harness:
            continue
        lexical = (overlap / max(1, len(q_tokens))) if q_tokens else 0.0
        score = (
            lexical * 6.0
            + tag_hits * 2.0
            + same_harness * 2.5
            + float(item.pop("_embedding", 0.0)) * 4.0
        )
        priority = status_priority(item, family)
        scored.append(((priority, score, int(item.get("retrieval_count") or 0)), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [item for _rank, item in scored[: max(1, min(int(k), 50))]]
    if results:
        now = _now()
        conn.executemany(
            "UPDATE sim_problems SET retrieval_count = retrieval_count + 1, last_used_at = ? WHERE problem_id = ?",
            [[now, item["problem_id"]] for item in results],
        )
        conn.commit()
    return results


def history(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    harness: str | None = None,
    include_retired: bool = True,
    is_admin: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """Full proposer view, including retired and regressed attempts.

    Proposers must see why a rule exists and which fixes already failed; this
    returns every attempt, while runtime retrieval excludes retired lessons.
    """
    owner = str(user_id or "").strip().lower()
    if not owner:
        raise ValueError("SIM history needs a user_id")
    clauses = ["((scope = 'system' AND (? = 1 OR requires_admin = 0)) OR (scope = 'user' AND user_id = ?))"]
    params: list[Any] = [int(bool(is_admin)), owner]
    if harness:
        clauses.append("harness = ?")
        params.append(normalize_harness(harness))
    if not include_retired:
        clauses.append("status != ?")
        params.append(STATUS_RETIRED)
    rows = conn.execute(
        f"SELECT * FROM sim_problems WHERE {' AND '.join(clauses)} "
        "ORDER BY harness, status, updated_at DESC LIMIT ?",
        [*params, max(1, min(int(limit), 2000))],
    ).fetchall()
    problems: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        if item is None:
            continue
        attempts = conn.execute(
            "SELECT * FROM sim_attempts WHERE problem_id = ? ORDER BY created_at DESC, attempt_id",
            [item["problem_id"]],
        ).fetchall()
        item["attempts"] = [attempt for a in attempts if (attempt := _attempt_to_dict(a)) is not None]
        problems.append(item)
    return {"count": len(problems), "problems": problems}


def consolidate(conn: sqlite3.Connection) -> dict[str, Any]:
    """Promote portable winners, mark model-specific rules and retire regressions.

    Called by the trainer's selector after outcomes are recorded. It is
    idempotent: running it repeatedly converges on the same statuses.
    """
    rows = conn.execute("SELECT * FROM sim_problems").fetchall()
    promoted: list[str] = []
    model_specific: list[str] = []
    retired: list[str] = []
    updated = 0
    now = _now()
    for row in rows:
        item = _row_to_dict(row)
        if item is None:
            continue
        old = str(item.get("status") or STATUS_CANDIDATE)
        verified = _json_list(item.get("verified_models"))
        regressed = _json_list(item.get("regressed_models"))
        new = _derive_status(verified, regressed)
        if new == old:
            continue
        conn.execute(
            "UPDATE sim_problems SET status = ?, updated_at = ? WHERE problem_id = ?",
            [new, now, item["problem_id"]],
        )
        updated += 1
        if new == STATUS_PORTABLE:
            promoted.append(item["problem_id"])
        elif new == STATUS_MODEL_SPECIFIC:
            model_specific.append(item["problem_id"])
        elif new == STATUS_RETIRED:
            retired.append(item["problem_id"])
    conn.commit()
    return {
        "checked": len(rows),
        "updated": updated,
        "promoted": promoted,
        "model_specific": model_specific,
        "retired": retired,
    }


def scope_owner(scope: str, user_id: str | None = None) -> str:
    """The owner column value a scope must use (System SIM is always default)."""
    normalize_scope(scope)
    return SYSTEM_SCOPE_OWNER if scope == SYSTEM_SCOPE else str(user_id or "").strip().lower()


SYSTEM_SCOPE_OWNER = SYSTEM_USER


def is_live(item: dict[str, Any], family: str) -> bool:
    """Whether one ledger row may be injected for a model family."""
    status = str(item.get("status") or STATUS_CANDIDATE)
    if status == STATUS_RETIRED:
        return False
    if status == STATUS_MODEL_SPECIFIC and not is_verified_for_family(item, family):
        return False
    return not is_regressed_for_family(item, family)
