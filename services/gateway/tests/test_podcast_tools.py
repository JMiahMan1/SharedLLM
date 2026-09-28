"""Regression: the podcast and speaker tools are advertised AND dispatchable.

Two different tool protocols reach the same dispatcher, and each has its own gate:

* the Raven `@type` protocol goes through ``agent_loop`` and is gated on
  ``ALLOWED_TOOLS`` before the action table is consulted. A name that is absent
  does not error - it falls through the Tier-3 ``difflib`` matcher, which
  **rewrites the call to a different tool** and dispatches it. That is bug #13
  (``ImageEditRequest`` -> ``climaterequest``).
* the external OpenAI-tools path (``external_agent``) goes through
  ``tool_registry.resolve_tool_call`` and never consults ``ALLOWED_TOOLS`` at
  all, so a tool advertised in ``get_tool_schemas()`` but absent from
  ``resolve_tool_call`` simply fails to resolve.

test_tool_whitelist_completeness.py already covers ``tool_builder._Tool`` and
``prose_tools._RAVEN_TOOL_TYPES``. Neither of those is where these three tools
live: they are declared in ``_RAVEN_TOOL_TABLE`` and as OpenAI schemas. This
file closes both of those gaps, so the next tool added in either style fails
here rather than in a mission.

Everything is read by AST or imported from tool_registry, which is a leaf module
(importing ``agent_loop`` constructs the FastAPI app and demands FERNET_KEY).
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path

import pytest

from services.gateway.agent_loop import ALLOWED_TOOLS
from services.gateway.tool_registry import (
    SVC_EXECUTION,
    TOOL_LIST_VOICES,
    TOOL_PODCAST_RENDER,
    TOOL_SPEAKER_IDENTIFY,
    get_raven_tool_schemas,
    get_tool_schemas,
    resolve_tool_call,
)

GATEWAY = Path(__file__).resolve().parents[1]
REGISTRY = GATEWAY / "tool_registry.py"

NEW_TOOLS = (TOOL_PODCAST_RENDER, TOOL_SPEAKER_IDENTIFY, TOOL_LIST_VOICES)
NEW_RAVEN_TYPES = ("PodcastRenderRequest", "SpeakerIdentifyRequest", "ListVoicesRequest")

# The two workspace-scoped ones write into the mission workspace; the voice
# listing is a read and must not demand one.
REQUIRES_WORKSPACE = {
    TOOL_PODCAST_RENDER: True,
    TOOL_SPEAKER_IDENTIFY: True,
    TOOL_LIST_VOICES: False,
}


def _normalise(raw: str) -> str:
    """The exact normalisation the action dispatcher applies."""
    return re.sub(r"[\s_]+", "", raw).lower()


def _raven_tool_table_names() -> set[str]:
    """The first element of every ``_RAVEN_TOOL_TABLE`` row, read as source.

    Parsed rather than imported so this cannot drift silently against an
    `if TYPE_CHECKING` guard or a lazy import in the module body.
    """
    tree = ast.parse(REGISTRY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_RAVEN_TOOL_TABLE":
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                break
            names = set()
            for row in node.value.elts:
                if isinstance(row, (ast.Tuple, ast.List)) and row.elts:
                    first = row.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
            return names
    raise AssertionError("_RAVEN_TOOL_TABLE not found in tool_registry.py")


# ---------------------------------------------------------------- Raven gate


def test_the_three_new_raven_types_are_whitelisted():
    """Without this, a model naming `PodcastRenderRequest` correctly gets its
    call fuzzy-rewritten to an unrelated tool - the bug-#13 failure mode."""
    missing = sorted(t for t in NEW_RAVEN_TYPES if _normalise(t) not in ALLOWED_TOOLS)
    assert not missing, (
        f"{missing} are in _RAVEN_TOOL_TABLE but absent from ALLOWED_TOOLS, so the "
        "fuzzy tier will rewrite the call to an unrelated tool"
    )


def test_no_raven_tool_table_entry_is_fuzzy_matched_away():
    """The property, for the whole table rather than the three new names: the
    Tier-3 matcher must return every advertised tool unchanged."""
    hijacked: dict[str, str] = {}
    for raw in _raven_tool_table_names():
        norm = _normalise(raw)
        if norm not in ALLOWED_TOOLS:
            hijacked[raw] = "<not whitelisted -> falls through to the alias tiers>"
            continue
        match = difflib.get_close_matches(norm, list(ALLOWED_TOOLS), n=1, cutoff=0.6)
        if match and match[0] != norm:
            hijacked[raw] = match[0]
    assert not hijacked, f"advertised Raven tools the fuzzy tier would rewrite: {hijacked}"


def test_the_raven_dispatch_table_agrees_with_the_registry_table():
    """agent_loop carries its own ``name -> (service, path)`` map, and the
    registry carries ``name -> path``. A tool in one and not the other resolves
    and then 404s."""
    source = (GATEWAY / "agent_loop.py").read_text()
    for name in NEW_RAVEN_TYPES:
        assert f'"{_normalise(name)}"' in source, f"{name} has no agent_loop dispatch entry"


def test_the_three_are_workspace_forced_only_where_that_makes_sense():
    """`_ws_actions` force-injects the mission's workspace_id, so a read-only
    tool listed there would get a workspace it does not need."""
    source = (GATEWAY / "agent_loop.py").read_text()
    block = source.split("_ws_actions = {", 1)[1].split("}", 1)[0]
    for name in ("podcastrenderrequest", "speakeridentifyrequest"):
        assert f'"{name}"' in block
    assert '"listvoicesrequest"' not in block, "listing voices is a read; it must not demand a workspace"


# ------------------------------------------------------------ external-agent gate


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_each_tool_is_in_the_openai_schema_list(name):
    assert name in {t["function"]["name"] for t in get_tool_schemas()}


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_each_tool_resolves_to_the_execution_service(name):
    resolved = resolve_tool_call(name, {"workspace_id": "w1", "script": "HOST A: hi", "audio_path": "a.wav"})
    assert resolved.service == SVC_EXECUTION
    assert resolved.path in {"/execute/podcast_render", "/execute/speaker_identify", "/execute/list_voices"}


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_the_workspace_requirement_matches_the_registry(name):
    """resolve_tool_call must agree with requires_workspace, because
    run_sharedllm_tool refuses a workspace-scoped call that has no workspace."""
    args = {"workspace_id": "w1", "script": "s", "audio_path": "a.wav"} if name != TOOL_LIST_VOICES else {}
    assert resolve_tool_call(name, args).requires_workspace is REQUIRES_WORKSPACE[name]


def test_the_render_schema_carries_the_fields_the_handler_needs():
    """The handler reads these by name. A schema that drops one silently
    downgrades the tool: no bed, no clone, or no way to choose the output."""
    schema = next(t for t in get_tool_schemas() if t["function"]["name"] == TOOL_PODCAST_RENDER)
    props = schema["function"]["parameters"]["properties"]
    assert {"script", "pair_id", "output_path", "bed_preset", "voice_profiles"} <= set(props)
    assert schema["function"]["parameters"]["required"] == ["workspace_id", "script"]


def test_the_identify_schema_documents_the_optional_threshold():
    """The threshold is optional on purpose - the audio server derives one from
    its own speakers. A schema that marked it required would force every caller
    to invent a constant."""
    schema = next(t for t in get_tool_schemas() if t["function"]["name"] == TOOL_SPEAKER_IDENTIFY)
    props = schema["function"]["parameters"]["properties"]
    assert "threshold" in props
    assert "threshold" not in schema["function"]["parameters"].get("required", [])


def test_the_voice_profiles_schema_explains_the_clone_key():
    """The clone only attaches when the key is exactly
    `host_clone_<pair_id>_<a|b>`, which is not guessable from the field name.
    The description is the only place a model learns it."""
    schema = next(t for t in get_tool_schemas() if t["function"]["name"] == TOOL_PODCAST_RENDER)
    desc = schema["function"]["parameters"]["properties"]["voice_profiles"]["description"]
    assert "host_clone_" in desc


def test_the_raven_schemas_also_carry_the_three():
    """get_raven_tool_schemas() keeps the PascalCase action name, so compare
    against it un-normalised - normalising first is what made this fail once."""
    names = {t["function"]["name"] for t in get_raven_tool_schemas()}
    for raw in NEW_RAVEN_TYPES:
        assert raw in names, f"{raw} missing from get_raven_tool_schemas()"


def test_the_prompt_error_tool_table_names_the_new_tools():
    """The schema-error the model reads on a bad call is a hand-maintained
    table. tool_registry's three new rows have to be in it too, or the model is
    told the exact list and the list is incomplete."""
    source = (GATEWAY / "agent_loop.py").read_text()
    assert '"Voice Tools": [' in source
    row = source.split('"Voice Tools": [', 1)[1].split("]", 1)[0]
    for name in re.findall(r'"([^"]+)"', row):
        assert name in ALLOWED_TOOLS, f"the error-message tool table advertises {name!r}, which is not a tool"


# --------------------------------------------------------------------------
# The dispatch timeout must exceed the handler's own budget
# --------------------------------------------------------------------------

#: Every Raven action whose handler blocks for longer than the standard ceiling.
#: Each is a synthetic job - rendering speech, converting an image, re-synthesizing
#: every chapter of a book - so a client-side timeout does not save any time: the
#: worker keeps going and the model, told the call failed, dispatches the same
#: job again.
LONG_RUNNING_ACTIONS = {
    "imageeditrequest": 590.0,
    "audiobookregeneraterequest": 5400.0,
    "podcastrenderrequest": 2400.0,
    "speakeridentifyrequest": 180.0,
}
STANDARD_DISPATCH_TIMEOUT_S = 120.0


def _dispatch_timeout_expr() -> ast.expr:
    """The dispatch-timeout expression node, straight out of the parsed module.

    Locating it by AST rather than by slicing text: the ladder is written across
    several lines, and a text slice that stops at the first newline captures one
    line of it rather than the whole conditional.
    """
    module = ast.parse((GATEWAY / "agent_loop.py").read_text())
    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_dispatch_timeout" for t in node.targets
        ):
            return node.value
    raise AssertionError("agent_loop no longer assigns _dispatch_timeout")  # pragma: no cover


def _dispatch_timeout_for(action: str) -> float:
    """The ceiling this action actually gets, by evaluating the real expression.

    Read rather than imported (importing ``agent_loop`` builds the FastAPI app and
    demands FERNET_KEY) and evaluated rather than re-parsed, so the test cannot
    pass by agreeing with a second copy of the ladder that a future edit leaves
    behind. The expression is a literal-only ternary over ``lookup_action``.
    """
    expr = ast.Expression(body=ast.fix_missing_locations(_dispatch_timeout_expr()))
    code = compile(expr, "<dispatch-timeout>", "eval")
    return float(eval(code, {"__builtins__": {}}, {"lookup_action": action}))  # noqa: S307


def _handler_budget(action: str) -> float | None:
    """The aiohttp timeout the execution handler budgets for this action."""
    source = (GATEWAY.parent / "execution" / "handlers" / "podcast.py").read_text()
    for const, name in (
        ("_RENDER_TIMEOUT_S", "podcastrenderrequest"),
        ("_IDENTIFY_TIMEOUT_S", "speakeridentifyrequest"),
        ("_VOICES_TIMEOUT_S", "listvoicesrequest"),
    ):
        if name == action and f"{const} = " in source:
            return float(source.split(f"{const} = ", 1)[1].split("\n", 1)[0].rstrip(".f"))
    return None


@pytest.mark.parametrize("action,budget", sorted(LONG_RUNNING_ACTIONS.items()))
def test_a_long_running_action_has_a_dispatch_timeout_row(action, budget):
    """A podcast render was dispatched with the standard 120 s ceiling while the
    handler budgets 1800 s, so every episode past two minutes was abandoned by the
    client while the dashboard went on mixing - and the model was told it had
    failed, so it re-dispatched the same half-hour job."""
    assert _dispatch_timeout_for(action) == budget


def test_the_dispatch_timeout_exceeds_the_handler_budget_it_covers():
    """Strictly greater, not greater-or-equal. A tie is a race and the loser is
    whichever side fires first, so the upstream's own 422/500 mapping gets
    replaced by a client TimeoutError that says nothing useful."""
    for action in ("podcastrenderrequest", "speakeridentifyrequest"):
        budget = _handler_budget(action)
        assert budget is not None, f"no handler budget found for {action}"
        assert _dispatch_timeout_for(action) > budget, (action, budget, _dispatch_timeout_for(action))


def test_a_list_voices_call_does_not_need_a_long_dispatch_timeout():
    """It is a read of two JSON lists; the standard ceiling is correct and a
    longer one would only delay the report that it is unreachable."""
    assert _handler_budget("listvoicesrequest") < STANDARD_DISPATCH_TIMEOUT_S


def test_every_podcast_action_is_covered_by_the_ladder():
    """Generalises the check: a new podcast action whose handler outlasts the
    standard ceiling but has no row falls back to 120 s silently, and that silent
    fallback is the whole failure mode."""
    from services.gateway.tool_registry import TOOL_LIST_VOICES, TOOL_PODCAST_RENDER, TOOL_SPEAKER_IDENTIFY

    for const, action in (
        (TOOL_PODCAST_RENDER, "podcastrenderrequest"),
        (TOOL_SPEAKER_IDENTIFY, "speakeridentifyrequest"),
        (TOOL_LIST_VOICES, "listvoicesrequest"),
    ):
        assert const  # the const is what a reader would add the row next to
        budget = _handler_budget(action)
        assert budget is not None, action
        # >=, not >: a budget of exactly the standard ceiling is a tie, and a tie
        # still needs its own row - the client would race the upstream.
        if budget >= STANDARD_DISPATCH_TIMEOUT_S:
            assert _dispatch_timeout_for(action) > budget, (action, budget)
        else:
            assert _dispatch_timeout_for(action) == STANDARD_DISPATCH_TIMEOUT_S, action
