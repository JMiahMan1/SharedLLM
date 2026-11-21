"""The secret scrubber for teacher traces. Standard library only.

Teacher traces are transcripts of strong cloud models (opencode, Cline) working
on this repository. They are full of things that must never be stored, retrieved
by a mission, or trained into a model: API keys a command printed, a decrypted
column, the contents of ``.env``, an email address, the household's names.

The scrubber runs on the machine that holds the transcripts, before anything is
written or sent, and the gateway runs :func:`leak_check` again on what arrives.
It works in three layers, strictest first:

1. **Drop the step.** A step whose input or output touched ``.env``, key
   decryption, an identity database's encrypted columns, ssh keys, a process
   environment dump, or whose output carried a recognisable credential, is
   removed whole: masking a decrypted key dump would leave its shape behind.
2. **Drop the trace.** A task whose *goal* is about secrets (rotate keys,
   decrypt, read ``.env``), or that lost too many steps to rule 1, or that still
   fails :func:`leak_check` after masking, leaves only a stub with counts.
3. **Mask what remains.** Provider keys, bearer tokens, Fernet keys and tokens,
   JWTs, private keys, ``NAME=value`` assignments with a secret-looking name,
   URL passwords, high-entropy strings, emails, phone numbers, IP addresses
   (unless told to keep them), home-directory user names, every literal value
   found in the local ``.env`` files, and the household names listed in a local
   file. Names are never written in code: they come from that file.

Nothing here calls a model or the network. ``SCRUB_VERSION`` is bumped whenever
a rule changes, so a trace scrubbed by older rules can be told apart (and the
gateway refuses it).
"""
from __future__ import annotations

import math
import pathlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Bump on any rule change. The gateway refuses traces scrubbed by another version.
SCRUB_VERSION = "1"

#: A task that loses more than this share of its steps is dropped whole.
MAX_DROPPED_SHARE = 0.25

#: ...or more than this many steps for touching secrets.
MAX_DROPPED_STEPS = 3


def _mask(kind: str) -> str:
    return f"[REDACTED:{kind}]"


# ---------------------------------------------------------------------------
# Layer 1 and 2: what makes a step, or a whole task, too sensitive to keep
# ---------------------------------------------------------------------------

#: Env files. ``.env.example`` style templates carry no values and are allowed.
_ENV_FILE = re.compile(
    r"(?<![\w.-])(?:[\w.-]*/)?\.env(?:\.(?!example\b|sample\b|template\b|dist\b)[\w-]+)?(?![\w.-])"
    r"|(?:(?<=[\s/'\"=])|^)(?!process\.env|meta\.env)[\w-]+\.env(?=$|[\s'\"])",
    re.M,
)

_SENSITIVE_INPUT = re.compile(
    r"(?ix)"
    r"\bfernet\b|\bdecrypt\w*|\bencrypted_\w+|\w+_encrypted\b|\bencryption_key\b|\bfernet_key\b"
    r"|\binternal_secret\b|\bx-internal-secret\b"
    r"|\bprintenv\b|(?:^|[;&|]\s*)env\s*(?:$|[|;&])|\bdocker\s+(?:compose\s+)?(?:inspect|exec\b[^\n]*\benv\b)"
    r"|/proc/\d+/environ|\bsecrets?\.(?:ya?ml|json|toml)\b|\bauth\.json\b|\bcredentials?\.json\b"
    r"|\.ssh/|\bid_(?:rsa|ed25519|ecdsa)\b|\.pem\b|\bprivate[\s_-]?key\b"
    r"|\bsecurity\s+find-(?:generic|internet)-password\b|\bkeychain\b"
    r"|\bidentity\w*\.(?:db|sqlite3?)\b"
)

#: A goal about these is not a coding lesson; the whole task is dropped.
_SENSITIVE_GOAL = re.compile(
    r"(?ix)"
    r"\b(?:rotate|rotating|rotation\s+of|leak(?:ed)?|decrypt\w*|re-?encrypt\w*)\b[^.\n]{0,60}\b(?:keys?|secrets?|tokens?|passwords?|credentials?)\b"
    r"|\b(?:keys?|secrets?|tokens?|passwords?|credentials?)\b[^.\n]{0,40}\b(?:rotat\w+|decrypt\w*|leak\w*)\b"
    r"|\bdecrypt\w*|\bfernet\b|\bprivate[\s_-]?key\b|\bpassword\s+reset\b|\binternal_secret\b"
)


# ---------------------------------------------------------------------------
# Layer 3: masking
# ---------------------------------------------------------------------------

#: Credentials with a recognisable shape. Any of these in a step's output drops
#: the step; anywhere else it is masked; after masking none may remain.
_STRICT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)),
    ("api_key", re.compile(r"\bsk-(?:or-v1-|ant-[a-z0-9]+-|proj-)?[A-Za-z0-9_-]{16,}")),
    ("api_key", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("api_key", re.compile(r"\bhf_[A-Za-z0-9]{20,}")),
    ("api_key", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("api_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api_key", re.compile(r"\b(?:glpat|nvapi|pplx|gsk|csk|r8|fw)[-_][A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("fernet_token", re.compile(r"\bgAAAAA[A-Za-z0-9_-]{40,}={0,2}")),
    ("fernet_key", re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])")),
)

_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
_AUTH_HEADER = re.compile(
    r"""(?i)((?:authorization|x-internal-secret|x-api-key|api-key|cookie|set-cookie)["']?\s*[:=]\s*["']?)"""
    r"""(?!\[REDACTED)([^\s"',}]{4,}(?:\s+[^\s"',}]{8,})?)"""
)
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]{3,})(@)")

#: NAME=value with a secret-looking name. Upper-case names are env style and
#: always masked; others only when the value is a quoted literal, so code such
#: as ``password = form.password`` survives intact.
_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:secret|token|passw(?:or)?d|passwd|pwd|api[_-]?key|apikey|access[_-]?key|private[_-]?key|fernet|encryption[_-]?key|credential|client[_-]?secret|auth[_-]?key|session[_-]?key|signing[_-]?key)[A-Za-z0-9_.-]*"
_ENV_ASSIGN = re.compile(
    r"(?m)(^|[\s;'\"(])((?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|API_KEY|APIKEY|ACCESS_KEY|PRIVATE_KEY|FERNET|ENCRYPTION_KEY|CREDENTIAL|AUTH_KEY|SIGNING_KEY)[A-Z0-9_]*\s*=\s*)"
    r"(?!\$\{|\$\(|\[REDACTED)(\"[^\"\n]*\"|'[^'\n]*'|[^\s'\"#;]+)"
)
_QUOTED_ASSIGN = re.compile(
    r"(?i)([\"']?" + _SECRET_NAME + r"[\"']?\s*[:=]\s*)([\"'])(?!\[REDACTED)([^\"'\n]{6,})([\"'])"
)

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_PHONE = re.compile(
    r"(?<![\w.:/-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w.-]*\d)"
    r"|(?<![\w.:/-])\+\d{1,3}[\s.-]?\d{2,4}[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w-])"
)
_IPV4 = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.]*\d)")
_HOME_DIR = re.compile(r"(/Users/|/home/|C:\\\\Users\\\\|C:\\Users\\)([^/\\\s'\"]+)")

#: Long hex runs: INTERNAL_SECRET style tokens, OpenRouter key bodies, digests.
#: Exactly 40 hex characters is a git commit and is kept.
_HEX_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{32,39}|[0-9a-fA-F]{41,})(?![A-Za-z0-9])")
_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_+=-])[A-Za-z0-9_+=-]{24,}(?![A-Za-z0-9_+=-])")

#: Placeholders the scrubber itself writes.
_PLACEHOLDER = re.compile(r"\[REDACTED:[a-z_]+\]")


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _looks_random(token: str) -> bool:
    """A long token that reads like a key rather than an identifier or a word."""
    if "_" in token and token.replace("_", "").isalpha():
        return False  # snake_case identifier
    if "-" in token and token.replace("-", "").isalpha():
        return False  # kebab-case word
    has_digit = any(c.isdigit() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    if not has_digit or not (has_upper or has_lower):
        return False
    if not (has_upper and has_lower) and sum(c.isdigit() for c in token) < 4:
        return False
    return shannon_entropy(token) >= 4.0


@dataclass
class ScrubConfig:
    """What the scrubber knows beyond its patterns. Loaded from local files only."""

    names: tuple[str, ...] = ()
    literals: tuple[str, ...] = ()
    mask_ips: bool = True
    names_loaded: bool = False

    def __post_init__(self) -> None:
        names = sorted({n.strip() for n in self.names if n and len(n.strip()) >= 2}, key=len, reverse=True)
        self.names = tuple(names)
        self._name_re = (
            re.compile(r"(?i)(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])")
            if names
            else None
        )
        literals = sorted({v for v in self.literals if v and len(v) >= 6}, key=len, reverse=True)
        self.literals = tuple(literals)


def load_names(path: str | pathlib.Path) -> list[str]:
    """Household names, one per line (``#`` comments allowed). The file must exist."""
    target = pathlib.Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(
            f"The household names file {target} does not exist. Create it (one name per line, "
            "it is never committed) or pass --no-names to scrub without names."
        )
    names: list[str] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    if not names:
        raise ValueError(f"The household names file {target} lists no names.")
    return names


def load_env_literals(paths: Iterable[str | pathlib.Path]) -> list[str]:
    """Every value in the given ``.env`` files, to be masked wherever it appears."""
    values: list[str] = []
    for path in paths:
        target = pathlib.Path(path).expanduser()
        if not target.is_file():
            continue
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.removeprefix("export").strip()
            value = value.strip().strip('"').strip("'")
            if len(value) < 6:
                continue
            # Only values that are secrets: a plain setting such as a branch
            # name would otherwise be masked in every trace that mentions it.
            secret_name = re.fullmatch(r"(?i)" + _SECRET_NAME, name) is not None
            if secret_name or _looks_random(value) or ("://" in value and "@" in value):
                values.append(value)
    return values


@dataclass
class ScrubStats:
    masked: Counter[str] = field(default_factory=Counter)
    dropped_steps: int = 0
    dropped_reasons: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "masked": dict(sorted(self.masked.items())),
            "dropped_steps": self.dropped_steps,
            "dropped_reasons": dict(sorted(self.dropped_reasons.items())),
        }


def find_strict(text: str, config: ScrubConfig | None = None, *, include_hex: bool = False) -> list[str]:
    """Kinds of credential present in ``text`` (empty when clean).

    Long hex runs are only counted with ``include_hex``: a docker digest in a
    command's output is no reason to drop the step (it is masked instead), but
    one left over after masking is a leak.
    """
    found: list[str] = []
    for kind, pattern in _STRICT_PATTERNS:
        if pattern.search(text):
            found.append(kind)
    if config is not None:
        if any(literal in text for literal in config.literals):
            found.append("env_value")
        if config._name_re is not None and config._name_re.search(text):
            found.append("person")
    if include_hex and _HEX_RUN.search(text):
        found.append("hex_secret")
    return found


def scrub_text(text: str, config: ScrubConfig, stats: ScrubStats | None = None) -> str:
    """Mask every secret-shaped thing in ``text``."""
    if not text:
        return text
    stats = stats if stats is not None else ScrubStats()

    def sub(pattern: re.Pattern[str], kind: str, value: str, repl: Any = None) -> str:
        def _r(match: re.Match[str]) -> str:
            stats.masked[kind] += 1
            if repl is None:
                return _mask(kind)
            return repl(match)

        return pattern.sub(_r, value)

    out = text
    for literal in config.literals:
        if literal in out:
            stats.masked["env_value"] += out.count(literal)
            out = out.replace(literal, _mask("env_value"))
    for kind, pattern in _STRICT_PATTERNS:
        out = sub(pattern, kind, out)
    out = sub(_BEARER, "bearer", out, lambda m: m.group(1) + _mask("bearer"))
    out = sub(_AUTH_HEADER, "auth_header", out, lambda m: m.group(1) + _mask("auth_header"))
    out = sub(_URL_PASSWORD, "url_password", out, lambda m: m.group(1) + _mask("url_password") + m.group(3))
    out = sub(_ENV_ASSIGN, "env_assignment", out, lambda m: m.group(1) + m.group(2) + _mask("env_assignment"))
    out = sub(
        _QUOTED_ASSIGN, "secret_assignment", out,
        lambda m: m.group(1) + m.group(2) + _mask("secret_assignment") + m.group(4),
    )
    out = sub(_HEX_RUN, "hex_secret", out)

    def _entropy(match: re.Match[str]) -> str:
        token = match.group(0)
        if _PLACEHOLDER.search(token) or not _looks_random(token):
            return token
        stats.masked["high_entropy"] += 1
        return _mask("high_entropy")

    out = _CANDIDATE.sub(_entropy, out)
    out = sub(_EMAIL, "email", out)
    out = sub(_PHONE, "phone", out)
    if config.mask_ips:
        out = sub(_IPV4, "ip", out)
    out = sub(_HOME_DIR, "user", out, lambda m: m.group(1) + "[USER]")
    if config._name_re is not None:
        out = sub(config._name_re, "person", out, lambda m: "[PERSON]")
    return out


def scrub_value(value: Any, config: ScrubConfig, stats: ScrubStats) -> Any:
    """Scrub every string inside a JSON-like value (keys too)."""
    if isinstance(value, str):
        return scrub_text(value, config, stats)
    if isinstance(value, list):
        return [scrub_value(item, config, stats) for item in value]
    if isinstance(value, dict):
        return {scrub_text(str(k), config, stats): scrub_value(v, config, stats) for k, v in value.items()}
    return value


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_flatten(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(_flatten(v) for v in value)
    return "" if value is None else str(value)


def step_drop_reason(step: dict[str, Any], config: ScrubConfig) -> str | None:
    """Why a step must be dropped whole, or ``None`` when it may be masked and kept."""
    args_text = _flatten(step.get("args"))
    output = _flatten(step.get("output"))
    thought = _flatten(step.get("thought"))
    if _ENV_FILE.search(args_text):
        return "env_file"
    if _SENSITIVE_INPUT.search(args_text):
        return "secret_access"
    if _ENV_FILE.search(output) and re.search(r"(?m)^[A-Z][A-Z0-9_]{2,}=\S", output):
        return "env_file"
    strict = find_strict(output, config) + find_strict(args_text, config)
    strict = [kind for kind in strict if kind != "person"]
    if strict:
        return "credential_in_output"
    if re.search(r"(?m)^[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|FERNET|ENCRYPTION_KEY)[A-Z0-9_]*=\S{6,}", output):
        return "env_dump"
    if _SENSITIVE_INPUT.search(thought) and re.search(r"(?i)\bdecrypt", thought):
        return "secret_access"
    return None


def goal_is_sensitive(goal: str) -> bool:
    return bool(_SENSITIVE_GOAL.search(goal or "") or _ENV_FILE.search(goal or ""))


def leak_check(value: Any, config: ScrubConfig | None = None) -> list[str]:
    """What a scrubbed value still leaks (kinds only, never the values)."""
    text = _flatten(value)
    found = set(find_strict(text, config, include_hex=True))
    for match in _CANDIDATE.finditer(text):
        if _looks_random(match.group(0)) and not _PLACEHOLDER.search(match.group(0)):
            found.add("high_entropy")
            break
    if _EMAIL.search(text):
        found.add("email")
    return sorted(found)


#: Fields of a trace that carry free text.
_TEXT_FIELDS = ("goal", "final_answer", "project", "title")


def scrub_trace(trace: dict[str, Any], config: ScrubConfig) -> dict[str, Any]:
    """Scrub one normalised trace. Returns the clean trace or a content-free stub.

    The stub keeps only what the Lab needs to show that a task was seen and why
    it was not kept: id, source, session, model, dates, counts and reasons.
    """
    stats = ScrubStats()
    steps = list(trace.get("steps") or [])
    kept: list[dict[str, Any]] = []
    for step in steps:
        reason = step_drop_reason(step, config)
        if reason:
            stats.dropped_steps += 1
            stats.dropped_reasons[reason] += 1
            continue
        kept.append(step)

    drop_reason: str | None = None
    if goal_is_sensitive(str(trace.get("goal") or "")):
        drop_reason = "sensitive_goal"
    elif stats.dropped_steps >= MAX_DROPPED_STEPS or (
        steps and stats.dropped_steps / len(steps) > MAX_DROPPED_SHARE
    ):
        drop_reason = "too_many_secret_steps"
    elif _SENSITIVE_INPUT.search(str(trace.get("final_answer") or "")) and re.search(
        r"(?i)\bdecrypt", str(trace.get("final_answer") or "")
    ):
        drop_reason = "sensitive_answer"

    clean: dict[str, Any] = {}
    if drop_reason is None:
        clean = dict(trace)
        for name in _TEXT_FIELDS:
            if isinstance(clean.get(name), str):
                clean[name] = scrub_text(clean[name], config, stats)
        clean["steps"] = [scrub_value(step, config, stats) for step in kept]
        clean["files"] = scrub_value(list(trace.get("files") or []), config, stats)
        clean["outcome_evidence"] = scrub_value(list(trace.get("outcome_evidence") or []), config, stats)
        # Only the content is checked: ids (a session id is random-looking by
        # design) are not secrets and are not free text.
        residual = leak_check(
            {name: clean.get(name) for name in (*_TEXT_FIELDS, "steps", "files", "outcome_evidence")}, config
        )
        if residual:
            drop_reason = "residual:" + ",".join(residual)

    scrub = {
        "version": SCRUB_VERSION,
        "names_loaded": config.names_loaded,
        "ips_masked": config.mask_ips,
        **stats.to_dict(),
    }
    if drop_reason is not None:
        scrub["status"] = "dropped"
        scrub["drop_reason"] = drop_reason
        return {
            "schema": trace.get("schema"),
            "id": trace.get("id"),
            "source": trace.get("source"),
            "session_id": trace.get("session_id"),
            "task_index": trace.get("task_index"),
            "started_at": trace.get("started_at"),
            "model": trace.get("model"),
            "model_location": trace.get("model_location"),
            "outcome": trace.get("outcome"),
            "step_count": len(steps),
            "scrub": scrub,
        }
    scrub["status"] = "masked" if stats.masked or stats.dropped_steps else "clean"
    clean["step_count"] = len(clean["steps"])
    clean["scrub"] = scrub
    return clean


def _stub(trace: dict[str, Any], scrub: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": trace.get("schema"),
        "id": trace.get("id"),
        "source": trace.get("source"),
        "session_id": trace.get("session_id"),
        "task_index": trace.get("task_index"),
        "started_at": trace.get("started_at"),
        "model": trace.get("model"),
        "model_location": trace.get("model_location"),
        "outcome": trace.get("outcome"),
        "step_count": trace.get("step_count", len(trace.get("steps") or [])),
        "scrub": scrub,
    }


def session_touched_secrets(scrubbed: list[dict[str, Any]]) -> bool:
    """Did any task of a session ask about secrets or see a live credential?"""
    for trace in scrubbed:
        info = trace.get("scrub") or {}
        if info.get("drop_reason") in ("sensitive_goal", "sensitive_answer"):
            return True
        if (info.get("dropped_reasons") or {}).get("credential_in_output"):
            return True
        if str(info.get("drop_reason") or "").startswith("residual:"):
            return True
    return False


def apply_session_policy(scrubbed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every task of a cloud session that touched secrets.

    A cloud teacher that was shown a decrypted key or worked on credentials in
    one task may echo them in another in a form no pattern recognises, so the
    whole session is reduced to stubs, not only the task that was caught.
    Sessions on local models keep the per-task result: nothing left the machine.
    """
    sessions: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for trace in scrubbed:
        sessions.setdefault((trace.get("source"), trace.get("session_id")), []).append(trace)
    out: list[dict[str, Any]] = []
    for items in sessions.values():
        cloud = any(t.get("model_location") != "local" for t in items)
        if cloud and session_touched_secrets(items):
            for trace in items:
                scrub = dict(trace.get("scrub") or {})
                if scrub.get("status") != "dropped":
                    scrub["status"] = "dropped"
                    scrub["drop_reason"] = "session_touched_secrets"
                out.append(_stub(trace, scrub))
        else:
            out.extend(items)
    return out
