"""Numbered-prose tool-call parser for Raven's 35B model output.

The Raven autonomous protocol instructs the model to emit JSON tool calls with
an ``@type`` field, but the local 35B model frequently falls back to a numbered,
free-form prose plan instead, e.g.::

    1. WorkspaceCreateRequest name 'pushtest-verify' display_name 'pushtest'
    2. GitOperationRequest action 'repo_create' repo_name 'pus-verify' private true
    3. GitOperationRequest action 'add' path 'README.md'
    4. GitOperationRequest action 'commit' commit_message '6push' branch 'main'
    5. GitOperationRequest action 'push' branch 'main'

The standard JSON/XML extractors cannot parse this, so every tool call is
silently dropped and Raven "completes" a mission without doing any work. This
module recovers structured tool calls from that prose format so the same
``action_map`` dispatch used for JSON tool calls can execute them.
"""

from __future__ import annotations

import json
import re

# Tool type names Raven is instructed to emit. Order matters: longer / more
# specific prefixes first so "WorkspaceFileWriteRequest" is not truncated to
# "WorkspaceFile" by a shorter match.
_RAVEN_TOOL_TYPES = (
    "WorkspaceCreateRequest",
    "WorkspaceBootstrapRequest",
    "WorkspaceSettingsUpdateRequest",
    "WorkspaceShellRequest",
    "WorkspaceFileWriteRequest",
    "WorkspaceFileReadRequest",
    "WorkspaceFilePatchRequest",
    "WorkspacePortExposeRequest",
    "GitOperationRequest",
    "RavenRecallRequest",
    "RavenMissionRequest",
)

# Lowercase alias -> canonical @type. The model sometimes lowercases or
# hyphenates the type name.
_TYPE_ALIASES = {
    "workspacecreaterequest": "WorkspaceCreateRequest",
    "workspacebootstraprequest": "WorkspaceBootstrapRequest",
    "workspacesettingsupdaterequest": "WorkspaceSettingsUpdateRequest",
    "workspaceshellrequest": "WorkspaceShellRequest",
    "workspacefilewriterequest": "WorkspaceFileWriteRequest",
    "workspacefilereadrequest": "WorkspaceFileReadRequest",
    "workspacefilepatchrequest": "WorkspaceFilePatchRequest",
    "workspaceportexposerequest": "WorkspacePortExposeRequest",
    "workspace_expose_port": "WorkspacePortExposeRequest",
    "expose_port": "WorkspacePortExposeRequest",
    "gitoperationrequest": "GitOperationRequest",
    "ravenrecallrequest": "RavenRecallRequest",
    "ravenmissionrequest": "RavenMissionRequest",
}

# Matches a tool-type token, optionally suffixed with a digit (e.g.
# "GitOperationRequest2") or wrapped in prose. Captures the canonical type.
_TYPE_PATTERN = re.compile(
    r"(?P<type>" + "|".join(re.escape(t) for t in _RAVEN_TOOL_TYPES) + r")\d*\b",
    re.IGNORECASE,
)

# key 'single quoted value' | key "double quoted value" | key=value | key value
_PAIR_PATTERN = re.compile(
    r"""
    (?P<key>[A-Za-z_][\w-]*)                  # parameter name
    \s*[:=]?\s*                               # optional = or :
    (?:
        (?:'[^']*?'|"[^"]*?")                 # quoted value
      | [^\s'"]+                              # bare token (no spaces)
    )
    """,
    re.VERBOSE,
)

_QUOTED = re.compile(r"^['\"](.*)['\"]$")


def _split_segments(text: str) -> list[str]:
    """Split raw model text into per-tool segments on tool-type markers."""
    matches = list(_TYPE_PATTERN.finditer(text))
    if not matches:
        return []
    segments = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append(text[start:end])
    return segments


def _parse_pairs(segment: str) -> dict:
    """Extract key/value pairs from a single tool segment."""
    # Strip a leading list-number like "1." or "2)" or "3)"
    seg = re.sub(r"^\s*\d+[\.\)]\s*", "", segment)
    # Drop the leading type token itself.
    seg = _TYPE_PATTERN.sub("", seg, count=1).strip()

    pairs: dict = {}
    for pm in _PAIR_PATTERN.finditer(seg):
        key = pm.group("key").lower()
        raw = pm.group(0)[len(pm.group("key")):]
        raw = raw.lstrip(":= ").strip()
        qm = _QUOTED.match(raw)
        if qm:
            value: str | bool | None = qm.group(1)
        else:
            value = raw
        # The 35B model frequently wraps values in backticks (e.g. `repo_create`).
        # Strip them so downstream verb/name matching works.
        if isinstance(value, str):
            value = value.strip("`").strip()
            if value == "":
                value = None
        # Normalize bool/null ONLY when a real string value exists. A bare key
        # with no value (e.g. `GitOperationRequest action 'push' branch ''`) yields
        # None here — calling None.lower() used to crash the whole mission
        # finalization even when the real work already succeeded.
        if isinstance(value, str) and value:
            low = value.lower()
            if low in ("true", "false"):
                value = low == "true"
            elif low in ("none", "null", "nil"):
                value = None
        if key in ("private", "public", "isprivate", "ispublic"):
            # Normalize visibility keys to `private` bool for GitOperationRequest.
            if key in ("private", "isprivate"):
                pairs["private"] = value if isinstance(value, bool) else str(value).lower() not in ("false", "0", "no")
            else:
                pairs["public"] = value if isinstance(value, bool) else str(value).lower() not in ("false", "0", "no")
            continue
        pairs[key] = value
    return pairs


# Recognized Raven git verbs. When the model emits a `GitOperationRequest`
# wrapper in prose, the real verb usually sits in an `action`/`git_action` pair
# (e.g. `GitOperationRequest action 'repo_create' ...`). Prefer that over the
# wrapper type so the right handler dispatches.
_GIT_VERBS = {
    "status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset",
    "branch", "checkout", "clean", "show", "init", "remote", "remote_add",
    "repo_create", "repo_clone", "gh_noop",
}


def _resolve_prose_action(canonical: str, payload: dict) -> str:
    """Pick the routing `action` for a prose-recovered tool call.

    Prefer an explicit verb the model named inside the call (``action`` /
    ``git_action``) over the bare wrapper type. Strip stray backticks the model
    sometimes adds around values.
    """
    for key in ("action", "git_action", "gitaction", "operation"):
        v = payload.get(key)
        if isinstance(v, str):
            v = v.strip("`").strip().lower()
            if v in _GIT_VERBS:
                return v
    return canonical


def _normalize_segment(segment: str) -> dict | None:
    """Convert one prose tool segment into a normalized tool dict."""
    tm = _TYPE_PATTERN.search(segment)
    if not tm:
        return None
    raw_type = tm.group("type")
    canonical = _TYPE_ALIASES.get(raw_type.lower(), raw_type)
    payload = _parse_pairs(segment)
    action = _resolve_prose_action(canonical, payload)
    tool = {"@type": canonical, **payload}
    # Mirror the JSON path: surface the resolved verb as `action` so downstream
    # normalization/routing treats it uniformly.
    tool["action"] = action
    tool["payload"] = payload
    return tool


def extract_action_prose(text: str) -> list[dict] | None:
    """Extract a list of tool-call dicts from numbered-prose model output.

    Returns ``None`` when the text does not contain any recognizable Raven tool
    type (so callers can fall through to other extractors). Returns a list of
    normalized tool dicts (``@type`` + ``payload``) when it does.
    """
    if not text:
        return None
    # Guard: defer clean JSON to the dedicated JSON extractors. A fenced or bare
    # JSON object/array is NOT prose and would be mis-parsed by the heuristic
    # below (e.g. an object's quoted keys look like key/value pairs).
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return None
        except Exception:
            pass
    # Also skip if there is no recognizable Raven tool type at all.
    segments = _split_segments(text)
    if not segments:
        return None
    batch: list[dict] = []
    for seg in segments:
        tool = _normalize_segment(seg)
        if tool:
            batch.append(tool)
    return batch or None
