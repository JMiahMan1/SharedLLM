"""Which tasks may go to a cloud teacher, and which must stay on the local model.

The teacher pipeline only learns from work a cloud model was allowed to see.
This is the rule an agent (or a person choosing a model in opencode) applies
*before* the work starts, so a cloud model never sees secrets, household data
or production systems in the first place; the scrubber is the second line, not
the first. Standard library only, so ``scripts/teacher_route.py`` runs anywhere.

``route_task(text, paths)`` answers ``{"route": "cloud" | "local", "reasons": [...]}``.
Anything uncertain is local: a false "local" costs a slower answer, a false
"cloud" can leak.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from services.gateway.trainer import teacher_scrub as scrub

_HOUSEHOLD = re.compile(
    r"(?i)\b(?:famil(?:y|ies)|household|kids?|child(?:ren)?|son|daughter|wife|husband|spouse|chores?|allowance"
    r"|screen\s*time|location|geofence|life360|whereabouts|calendar\s+events?|contacts?|private\s+chat|messages?\s+from"
    r"|guardian|medical|health\s+records?|prescriptions?|photos?\s+of|camera\s+footage|recordings?|voice\s+samples?"
    r"|birthdays?|addresses|phone\s+numbers?|personal\s+data|pii)\b"
)
_PRODUCTION = re.compile(
    r"(?i)\b(?:production|prod\s+(?:db|database|server)|live\s+(?:server|database|data)|deploy(?:ing|ment)?\s+to"
    r"|ssh\s+\S+@|docker\s+exec|database\s+dump|db\s+dump|restore\s+(?:a\s+)?backup|backups?\b|migrat\w+\s+the\s+live"
    r"|user\s+accounts?|api\s+keys?|tokens?\s+for|credentials?)\b"
)
_LOCAL_PATHS = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.[\w-]+)?|data/|backups?/|secrets?[./]|services/identity/|identity\w*\.db"
    r"|private_chat\w*\.py|services/guardian/|\.ssh/)"
)
_PUBLIC_TEMPLATE = re.compile(r"(?i)\.env\.(?:example|sample|template)$")


def route_task(text: str, paths: Iterable[str] = (), *, names: Iterable[str] = ()) -> dict[str, Any]:
    """Decide where a task may run. ``names``: household names (from the local names file)."""
    reasons: list[str] = []
    text = text or ""
    if scrub.goal_is_sensitive(text) or scrub._SENSITIVE_INPUT.search(text):
        reasons.append("it involves secrets, keys or decryption")
    if _HOUSEHOLD.search(text):
        reasons.append("it involves household or personal data")
    if _PRODUCTION.search(text):
        reasons.append("it touches production systems, accounts or credentials")
    config = scrub.ScrubConfig(names=tuple(names))
    if config._name_re is not None and config._name_re.search(text):
        reasons.append("it names a household member")
    for path in paths:
        if _PUBLIC_TEMPLATE.search(path):
            continue
        if _LOCAL_PATHS.search(path):
            reasons.append(f"it touches {path}")
    if reasons:
        return {"route": "local", "reasons": list(dict.fromkeys(reasons))}
    return {"route": "cloud", "reasons": ["public code reasoning: no secrets, household data or production systems named"]}
