"""Regression: tools the app advertises must not be fuzzy-hijacked.

`ALLOWED_TOOLS` is a hard gate in the AgentLoop action dispatcher
(`if action_name not in ALLOWED_TOOLS:` at services/gateway/agent_loop.py). A
name that is absent does not error - it falls through to the Tier-3 fuzzy
matcher, which **rewrites the call to a different tool** and dispatches it.

Four tools were advertised to the model by `tool_builder._Tool` and dispatched
by `prose_tools`, yet missing from `ALLOWED_TOOLS`, so a model naming any of
them correctly got its call silently rewritten:

    ImageEditRequest       -> climaterequest        (edit a photo -> set the heating)
    ImageGenerationRequest -> gitoperationrequest   (make an image -> a git commit)
    OcrRequest             -> request               (read text -> a bare token)
    RavenMissionRequest    -> redisinspectrequest   (start a mission -> poke Redis)

Same class of bug as `sttrequest` -> `ttsrequest` (mission 14) and the git-verb
hijack, both already covered in test_git_op_routing.py. This file covers the
image / OCR / mission family, and generalises the check so the next tool added
to tool_builder without a whitelist entry fails here.
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path

from services.gateway.agent_loop import ALLOWED_TOOLS

GATEWAY = Path(__file__).resolve().parents[1]

# The four that were missing. Keep this list and the assertions below in step:
# adding a name here without fixing ALLOWED_TOOLS fails the exact-match test.
ADVERTISED_BUT_UNLISTED = (
    "ImageEditRequest",
    "ImageGenerationRequest",
    "OcrRequest",
    "RavenMissionRequest",
)

# Names that are deliberately in ALLOWED_TOOLS without a tool_builder entry:
# the bare-parameter and git-verb tokens the fuzzy tiers need to land on.
_INTENTIONALLY_UNADVERTISED = {"restart_service"}


def _normalise(raw: str) -> str:
    """The exact normalisation the action dispatcher applies."""
    return re.sub(r"[\s_]+", "", raw).lower()


def _tool_builder_types() -> set[str]:
    """The ``_Tool("XRequest", ...)`` declarations, read as source.

    Parsed rather than imported so this test does not drag in the tool_builder
    module graph; a regex over the AST is enough and cannot drift silently.
    """
    return {
        node.args[0].value
        for node in ast.walk(ast.parse((GATEWAY / "tool_builder.py").read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_Tool"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_each_previously_hijacked_tool_is_now_whitelisted():
    for raw in ADVERTISED_BUT_UNLISTED:
        assert _normalise(raw) in ALLOWED_TOOLS, (
            f"{raw} is advertised by tool_builder but absent from ALLOWED_TOOLS, "
            "so the fuzzy tier will rewrite the call to an unrelated tool"
        )


def test_no_advertised_tool_is_fuzzy_matched_away():
    """The property, not the four names: for every tool the app describes to the
    model, the Tier-3 matcher must return it unchanged.

    This is what makes the next tool_builder addition fail here instead of in
    production, where the symptom is "I asked for an image edit and the heating
    came on".
    """
    hijacked: dict[str, str] = {}
    for raw in _tool_builder_types():
        if raw.startswith("sharedllm_"):
            continue  # the OpenAI-schema names are dispatched separately
        norm = _normalise(raw)
        if norm not in ALLOWED_TOOLS:
            hijacked[raw] = "<not whitelisted -> falls through to the alias tiers>"
            continue
        match = difflib.get_close_matches(norm, list(ALLOWED_TOOLS), n=1, cutoff=0.6)
        if match and match[0] != norm:
            hijacked[raw] = match[0]
    assert not hijacked, f"advertised tools the fuzzy tier would rewrite: {hijacked}"


def test_the_prose_parsed_types_are_whitelisted_too():
    """`prose_tools` extracts these types from free-form prose, so they reach the
    same gate by a different route."""
    from services.gateway.prose_tools import _RAVEN_TOOL_TYPES

    missing = sorted(t for t in _RAVEN_TOOL_TYPES if _normalise(t) not in ALLOWED_TOOLS)
    assert not missing, f"prose-parsed tool types absent from ALLOWED_TOOLS: {missing}"


def test_the_error_message_tool_table_names_real_tools():
    """The schema-error the model sees when nothing matches is built from a
    hand-maintained table. It advertised `climate`, which is not a tool name
    anywhere - the model is told to use the exact name from the list, and the
    list is wrong."""
    source = (GATEWAY / "agent_loop.py").read_text()
    assert '"HA Tools": [' in source
    row = source.split('"HA Tools": [', 1)[1].split("]", 1)[0]
    names = re.findall(r'"([^"]+)"', row)
    for name in names:
        assert name in ALLOWED_TOOLS, f"the error-message tool table advertises {name!r}, which is not a tool"
