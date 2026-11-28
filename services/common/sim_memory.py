"""Two-tier Self-Improvement Memory (SIM) shared by every harness.

The design follows *Self-Improvement Memory Enables Continual Recursive
Self-Improvement* (arXiv:2611.18856): a compact, problem-grouped ledger of
rules, why they exist, and what happened when they were applied. The ledger is
split into two scopes so a shared lesson can never leak one person's private
phrasing into another person's prompt:

``system``
    Shared by every user and every harness. Personal data, secrets, paths and
    contact details are scrubbed before a system lesson is stored. A lesson
    may be tagged ``requires_admin`` so an admin-only rule or tool is never
    shown to a non-admin caller.

``user``
    Owned by exactly one ``user_id``. Personal phrasing, disambiguations,
    corrections and workspace habits live here and are only ever read back for
    that same user.

This module is deliberately pure: it imports only the standard library, so the
gateway's import-isolation tests and any harness can use it without pulling in
FastAPI, aiohttp, sqlite or the vector store.

A *problem* is the stable identity of a lesson (scope + user + harness +
category + signature). Attempts are the history of applying and evaluating
that lesson. Lifecycle statuses drive cross-model portability:

``candidate``
    Recorded, not yet verified.
``verified``
    Improved on one model family (and did not regress on the current family).
``portable``
    Improved on at least two model families with a low regression rate. These
    are the highest-priority hints across a model swap.
``model_specific``
    Improved on one family and regressed on another. Injected only for the
    families it helped, never for a family it regressed.
``retired``
    Consistently regressed or superseded. Kept in the ledger so proposer agents
    can see why a fix was abandoned, but never injected at runtime.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

#: The only two scopes. Anything else is a programming error, never silently
#: treated as shared (which would be a privacy leak).
SYSTEM_SCOPE = "system"
USER_SCOPE = "user"
SCOPES = (SYSTEM_SCOPE, USER_SCOPE)

#: System SIM is stored under this synthetic owner.
SYSTEM_USER = "default"

#: The three harnesses that read and write SIM.
HARNESSES = ("raven", "workspace", "satellite")

#: Hard character budgets for the compact runtime block. The satellite budget
#: is the tightest because its turn context is also carrying retrieved passages
#: and, for voice, a spoken-answer constraint.
HARNESS_CHAR_BUDGETS: dict[str, int] = {
    "satellite": 600,
    "workspace": 1200,
    "raven": 1600,
}
DEFAULT_CHAR_BUDGET = 1200

OUTCOME_PENDING = "pending"
OUTCOME_IMPROVED = "improved"
OUTCOME_NEUTRAL = "neutral"
OUTCOME_REGRESSED = "regressed"
OUTCOME_STATUSES = (OUTCOME_PENDING, OUTCOME_IMPROVED, OUTCOME_NEUTRAL, OUTCOME_REGRESSED)

STATUS_CANDIDATE = "candidate"
STATUS_VERIFIED = "verified"
STATUS_PORTABLE = "portable"
STATUS_MODEL_SPECIFIC = "model_specific"
STATUS_RETIRED = "retired"
LIFECYCLE_STATUSES = (
    STATUS_CANDIDATE,
    STATUS_VERIFIED,
    STATUS_PORTABLE,
    STATUS_MODEL_SPECIFIC,
    STATUS_RETIRED,
)

#: Retrieval priority. ``portable`` wins over every other live status.
STATUS_PRIORITY: dict[str, int] = {
    STATUS_PORTABLE: 4,
    STATUS_VERIFIED: 3,
    STATUS_MODEL_SPECIFIC: 2,
    STATUS_CANDIDATE: 1,
    STATUS_RETIRED: 0,
}

#: Model families whose names do not contain the family token verbatim.
_FAMILY_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("kat-coder", "kwaipilot", "kilo-kat"), "kat-coder"),
    (("qwen", "qwq"), "qwen"),
    (("inkling",), "inkling"),
    (("gemini",), "gemini"),
    (("claude", "anthropic"), "claude"),
    (("deepseek",), "deepseek"),
    (("llama", "meta-llama"), "llama"),
    (("mistral", "mixtral", "codestral"), "mistral"),
    (("gemma",), "gemma"),
    (("grok",), "grok"),
    (("phi-", "phi3", "phi4"), "phi"),
    (("command-r", "cohere"), "cohere"),
    (("glm", "chatglm"), "glm"),
    (("kimi", "moonshot"), "kimi"),
    (("solar",), "solar"),
    (("yi-", "01-ai"), "yi"),
    (("gpt", "openai", "chatgpt", "o1-", "o3-", "o4-"), "gpt"),
    (("nemotron", "nvidia"), "nemotron"),
    (("granite", "ibm"), "granite"),
    (("olmo",), "olmo"),
    (("falcon",), "falcon"),
    (("vicuna",), "vicuna"),
    (("wizard",), "wizard"),
    (("internlm",), "internlm"),
    (("minimax",), "minimax"),
    (("ernie",), "ernie"),
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
#: ``scheme://user:password@host`` keeps the host but loses the credentials.
_URL_USERINFO_RE = re.compile(r"(https?://)[^/\s:@]+(?::[^/\s@]*)?@", re.IGNORECASE)
#: Common provider token shapes.
_TOKEN_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
#: ``api_key=...``, ``token: ...``, ``Bearer ...``.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|token|secret|"
    r"client[_-]?secret|password|passwd|pwd|bearer)\b(\s*[:=]\s*|\s+)([^\s,;\"']+)"
)
#: Absolute POSIX paths, including the macOS/private and home shapes.
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:])/(?:home|Users|private|var|tmp|opt|etc|root|srv|mnt|Volumes)/[^\s,;\"'<>()\[\]]+"
)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s,;\"']+\\)*[^\\\s,;\"']*")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?!\w)")
#: Long opaque strings (hex digests, base64 blobs) are usually keys or IDs.
_OPAQUE_RE = re.compile(r"\b(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")


def normalize_model_family(model: str | None) -> str:
    """Map a concrete model name to a stable family token.

    Examples::

        kwaipilot-kat-coder-v2-5-dev-iq4-nl -> kat-coder
        qwen3-6-35b-a3b-instruct            -> qwen
        inkling-2-mini                      -> inkling
        claude-sonnet-4-5                   -> claude
        deepseek-v3.2                       -> deepseek

    The family is the unit of cross-model portability: a lesson that helped two
    different families is portable, while one that helped only one family is
    not injected for another family it has never been tried on (or worse,
    regressed on).
    """
    raw = str(model or "").strip().lower()
    if not raw:
        return "unknown"
    # Provider prefixes ("openrouter/qwen3...") and tags ("...:latest") are not
    # part of the family.
    raw = raw.rsplit("/", 1)[-1]
    raw = raw.split(":", 1)[0]
    for needles, family in _FAMILY_ALIASES:
        if any(needle in raw for needle in needles):
            return family
    # Unknown: use the leading alphabetic token so two versions of the same
    # unknown model still group together ("acme-7b" and "acme-13b" -> "acme").
    match = re.match(r"[a-z][a-z0-9]*", raw)
    return match.group(0) if match else "unknown"


def scrub_system_sim_text(text: str) -> str:
    """Remove personal data and secrets from text destined for System SIM.

    System SIM is shared with every user, so it must never carry an email
    address, a token/key/password, a home path, a private IP or a phone number.
    The replacements are deliberately generic: the lesson keeps its technical
    meaning ("a workspace path was missing") without the identity behind it.
    """
    if not text:
        return ""
    cleaned = str(text)
    cleaned = _URL_USERINFO_RE.sub(r"\1[credentials]@", cleaned)
    cleaned = _EMAIL_RE.sub("[email]", cleaned)
    for pattern in _TOKEN_RES:
        cleaned = pattern.sub("[token]", cleaned)
    cleaned = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", cleaned)
    cleaned = _POSIX_PATH_RE.sub("[path]", cleaned)
    cleaned = _WINDOWS_PATH_RE.sub("[path]", cleaned)
    cleaned = _IPV4_RE.sub("[ip]", cleaned)
    cleaned = _PHONE_RE.sub("[phone]", cleaned)
    cleaned = _OPAQUE_RE.sub("[token]", cleaned)
    # Collapse whitespace introduced by the substitutions but keep paragraphs.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_scope(scope: str | None) -> str:
    value = str(scope or "").strip().lower()
    if value not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return value


def normalize_harness(harness: str | None) -> str:
    value = str(harness or "").strip().lower()
    if value not in HARNESSES:
        raise ValueError(f"harness must be one of {HARNESSES}, got {harness!r}")
    return value


def normalize_outcome_status(status: str | None) -> str:
    value = str(status or OUTCOME_PENDING).strip().lower()
    if value not in OUTCOME_STATUSES:
        raise ValueError(f"outcome_status must be one of {OUTCOME_STATUSES}, got {status!r}")
    return value


def normalize_lifecycle_status(status: str | None) -> str:
    value = str(status or STATUS_CANDIDATE).strip().lower()
    if value not in LIFECYCLE_STATUSES:
        raise ValueError(f"status must be one of {LIFECYCLE_STATUSES}, got {status!r}")
    return value


def stable_signature(text: str, *, max_chars: int = 180) -> str:
    """A stable grouping key: whitespace/case-normalized, bounded."""
    value = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return value[:max_chars]


def make_problem_id(
    *,
    scope: str,
    user_id: str,
    harness: str,
    category: str,
    signature: str,
) -> str:
    """The stable id for one problem: group attempts, never duplicate lessons."""
    scope = normalize_scope(scope)
    harness = normalize_harness(harness)
    owner = SYSTEM_USER if scope == SYSTEM_SCOPE else str(user_id or "").strip().lower()
    if not owner:
        raise ValueError("a user-scoped SIM problem needs a user_id")
    seed = "\x00".join((scope, owner, harness, str(category or "").strip().lower(), stable_signature(signature)))
    return "sim-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


@dataclass
class SimAttempt:
    """One application/evaluation of a lesson against a model family."""

    attempt_id: str
    problem_id: str
    harness: str
    model: str = ""
    model_family: str = ""
    domain: str = ""
    round: int = 0
    rationale: str = ""
    edit_or_rule: str = ""
    predicted_outcome: str = ""
    observed_outcome: str = ""
    outcome_status: str = OUTCOME_PENDING
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimProblem:
    """A problem-grouped lesson with its cross-model lifecycle state."""

    problem_id: str
    scope: str
    user_id: str
    harness: str
    category: str
    signature: str
    rationale: str
    edit_or_rule: str
    predicted_outcome: str = ""
    observed_outcome: str = ""
    outcome_status: str = OUTCOME_PENDING
    status: str = STATUS_CANDIDATE
    requires_admin: bool = False
    tags: list[str] = field(default_factory=list)
    verified_models: list[str] = field(default_factory=list)
    regressed_models: list[str] = field(default_factory=list)
    model: str = ""
    model_family: str = ""
    domain: str = ""
    round: int = 0
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        # JSON columns are decoded by the RAG layer; accept a raw string too.
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            import json

            try:
                loaded = json.loads(text)
            except ValueError:
                loaded = None
            if isinstance(loaded, list):
                return [str(v).strip().lower() for v in loaded if str(v).strip()]
        return [text.lower()] if text else []
    if isinstance(value, Iterable):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return []


def is_regressed_for_family(item: Any, family: str) -> bool:
    family = normalize_model_family(family)
    return family in _as_list(_get(item, "regressed_models", []))


def is_verified_for_family(item: Any, family: str) -> bool:
    family = normalize_model_family(family)
    return family in _as_list(_get(item, "verified_models", []))


def is_usable_for_family(item: Any, family: str, *, is_admin: bool = True) -> bool:
    """Whether a runtime prompt may show this lesson to this caller/model.

    Retired lessons never appear. Admin-only lessons appear only for admins.
    A model-specific lesson appears only for a family it actually improved
    (never for a family it regressed or was never tried on).
    """
    status = str(_get(item, "status", STATUS_CANDIDATE) or STATUS_CANDIDATE).lower()
    if status == STATUS_RETIRED:
        return False
    if bool(_get(item, "requires_admin", False)) and not is_admin:
        return False
    if is_regressed_for_family(item, family):
        return False
    if status == STATUS_MODEL_SPECIFIC:
        return is_verified_for_family(item, family)
    return True


def status_priority(item: Any, family: str) -> int:
    """Higher is better. Portability beats a same-family verification."""
    status = str(_get(item, "status", STATUS_CANDIDATE) or STATUS_CANDIDATE).lower()
    if status == STATUS_PORTABLE:
        return 5
    if is_verified_for_family(item, family):
        return 4
    if status == STATUS_VERIFIED:
        return 3
    if status == STATUS_MODEL_SPECIFIC:
        return 2
    return STATUS_PRIORITY.get(status, 1)


def _hint_label(item: Any, family: str) -> str:
    status = str(_get(item, "status", STATUS_CANDIDATE) or STATUS_CANDIDATE).lower()
    if status == STATUS_PORTABLE:
        return "portable"
    if is_verified_for_family(item, family):
        return "verified"
    if status == STATUS_MODEL_SPECIFIC:
        return "verified-other-model"
    if status == STATUS_VERIFIED:
        return "verified-other-model"
    return "unverified-on-current-family"


def _scope_label(item: Any) -> str:
    return "User" if str(_get(item, "scope", SYSTEM_SCOPE)).lower() == USER_SCOPE else "System"


def _lesson_text(item: Any) -> str:
    rule = str(_get(item, "edit_or_rule", "") or "").strip()
    rationale = str(_get(item, "rationale", "") or "").strip()
    if rule and rationale:
        return f"{rule} ({rationale})"
    return rule or rationale


def format_sim_block(
    items: Sequence[Any] | None,
    harness: str,
    current_model_family: str,
    *,
    is_admin: bool = False,
    preserve_order: bool = False,
) -> str:
    """Render the compact runtime SIM block, capped to the harness budget.

    Only per-request context may contain this block: the satellite harness
    appends it to the question, never to the static system prompt, so the
    model server's prompt-cache prefix stays warm.

    Portable lessons are shown first, then lessons verified on the current
    family, then lessons verified on another family (clearly marked
    unverified here), then candidates. Retired lessons and lessons that
    regressed on the current family are never rendered.

    ``preserve_order`` keeps the caller's relevance-first ordering from
    :func:`services.rag.sim.query` instead of re-sorting purely by status.
    That matters because an exact-matching verified lesson must outrank an
    unrelated portable one; callers that did no retrieval ranking keep the
    default portable-first sort.
    """
    harness = str(harness or "satellite").strip().lower()
    budget = HARNESS_CHAR_BUDGETS.get(harness, DEFAULT_CHAR_BUDGET)
    family = normalize_model_family(current_model_family)
    usable = [item for item in (items or []) if is_usable_for_family(item, family, is_admin=is_admin)]
    if not usable:
        return ""
    if not preserve_order:
        usable.sort(
            key=lambda item: (
                status_priority(item, family),
                int(_get(item, "retrieval_count", 0) or 0),
                str(_get(item, "problem_id", "")),
            ),
            reverse=True,
        )
    header = (
        f"SIM hints ({harness}; current model family: {family}). "
        "System lessons are shared by everyone; User lessons belong only to this user."
    )
    lines: list[str] = [header]
    used = len(header)
    shown = 0
    for item in usable:
        text = re.sub(r"\s+", " ", _lesson_text(item)).strip()
        if not text:
            continue
        label = _hint_label(item, family)
        line = f"- [{_scope_label(item)} · {label}] {text}"
        # Keep some room for the final "…" and never exceed the cap.
        remaining = budget - used - 1
        if remaining <= 0:
            break
        if len(line) > remaining:
            if shown == 0:
                line = line[: max(0, remaining - 1)].rstrip() + "…"
            else:
                break
        lines.append(line)
        used += len(line) + 1  # newline
        shown += 1
    if shown == 0:
        return ""
    block = "\n".join(lines)
    return block[:budget]


def summarize_history(items: Sequence[Any] | None) -> list[dict[str, Any]]:
    """A proposer-friendly view that keeps retired and regressed attempts.

    Proposer agents must see *why* a rule exists and which fixes already failed
    so they never propose the same failed rule again. Runtime injection uses
    :func:`format_sim_block`, which excludes retired lessons instead.
    """
    summary: list[dict[str, Any]] = []
    for item in items or []:
        summary.append(
            {
                "problem_id": str(_get(item, "problem_id", "")),
                "scope": str(_get(item, "scope", SYSTEM_SCOPE)),
                "user_id": str(_get(item, "user_id", SYSTEM_USER)),
                "harness": str(_get(item, "harness", "")),
                "category": str(_get(item, "category", "")),
                "status": str(_get(item, "status", STATUS_CANDIDATE)),
                "outcome_status": str(_get(item, "outcome_status", OUTCOME_PENDING)),
                "rationale": str(_get(item, "rationale", "")),
                "edit_or_rule": str(_get(item, "edit_or_rule", "")),
                "predicted_outcome": str(_get(item, "predicted_outcome", "")),
                "observed_outcome": str(_get(item, "observed_outcome", "")),
                "verified_models": _as_list(_get(item, "verified_models", [])),
                "regressed_models": _as_list(_get(item, "regressed_models", [])),
                "requires_admin": bool(_get(item, "requires_admin", False)),
            }
        )
    return summary


def merge_model_families(*families: str | None) -> list[str]:
    """Deduplicate model families while preserving the first-seen order."""
    seen: list[str] = []
    for family in families:
        token = normalize_model_family(family)
        if token and token != "unknown" and token not in seen:
            seen.append(token)
    return seen
