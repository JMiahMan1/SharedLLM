"""The gateway must have exactly one agent loop, and one set of agent limits.

Both invariants exist because this file used to lose them. `main.py` carried a
528-line duplicate `async def AgentLoop` with a hardcoded `MAX_TOOL_ITERATIONS =
30`; nothing ever called it (the orchestrator does a function-local import of the
real class), but two ADRs quoted its number, so the documentation confidently
described a limit the system never enforced. The same shape of bug produced
three different `raven_max_total_seconds` defaults: 1800 in the Identity seed,
1800 in `services/config.py`, and 14400 in the orchestrator's merge defaults.

Read by AST, not by import — importing `main` or `agent_loop` builds the FastAPI
app and needs the Identity service to be reachable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GATEWAY = REPO / "services" / "gateway"

pytestmark = pytest.mark.contract


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"))


def _assigned_int(rel: str, name: str) -> int | None:
    """The literal int a module-level `NAME = <int>` assigns, if any."""
    for node in _tree(rel).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if (
            any(isinstance(t, ast.Name) and t.id == name for t in targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            return node.value.value
    return None


def _assigned_str(rel: str, name: str) -> str | None:
    for node in _tree(rel).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if (
            any(isinstance(t, ast.Name) and t.id == name for t in targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _unwrap(value: ast.expr) -> ast.expr:
    """`cast(dict[str, str], {...})` — the orchestrator annotates its defaults, so
    the literal is the LAST positional arg, not the first (the first is the type)."""
    seen = 0
    while isinstance(value, ast.Call) and value.args and seen < 5:
        value = value.args[-1]
        seen += 1
    return value


def _dict_item_str(rel: str, dict_name: str, key: str) -> str | None:
    """`NAME = {... "key": "value" ...}` — the orchestrator's _DEFAULTS shape."""
    for node in _tree(rel).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(isinstance(t, ast.Name) and t.id == dict_name for t in targets):
            continue
        value = _unwrap(node.value)
        if not isinstance(value, ast.Dict):
            continue
        for k, v in zip(value.keys, value.values, strict=True):
            if isinstance(k, ast.Constant) and k.value == key and isinstance(v, ast.Constant):
                return v.value
    return None


def _seeded_settings() -> dict[str, str]:
    """The Identity GlobalSetting seed rows, read straight out of the AST."""
    seeds: dict[str, str] = {}
    for node in ast.walk(_tree("services/identity/models.py")):
        if not isinstance(node, ast.Dict):
            continue
        items: dict[str, object] = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                items[str(k.value)] = v.value
        key, val = items.get("key"), items.get("value")
        if isinstance(key, str) and isinstance(val, str):
            seeds[key] = val
    return seeds


def _agent_loop_definitions() -> dict[str, list[str]]:
    """rel path -> the argument names of every `AgentLoop` defined in it."""
    found: dict[str, list[str]] = {}
    for path in sorted(GATEWAY.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "AgentLoop":
                rel = str(path.relative_to(REPO))
                found.setdefault(rel, []).append(
                    ",".join(a.arg for a in node.args.args)
                )
    return found


# --- one loop, one definition -----------------------------------------------


def test_exactly_one_agent_loop_definition_in_the_gateway():
    found = _agent_loop_definitions()
    assert found == {
        "services/gateway/agent_loop.py": [
            # The signature callers rely on: the orchestrator passes mission_id
            # positionally and then rag_context / show_thinking / workspace_id /
            # history_log by keyword. A second, narrower copy is what silently
            # served the wrong MAX_TOOL_ITERATIONS to the docs.
            "query,selected_model,full_system,short_term,rag_user,creds,mission_id,"
            "rag_context,show_thinking,workspace_id,history_log"
        ]
    }, (
        f"expected a single AgentLoop in services/gateway/agent_loop.py; found {found}. "
        "A duplicate loop in main.py was removed because nothing called it, yet two "
        "ADRs quoted its MAX_TOOL_ITERATIONS."
    )


def test_main_py_does_not_redefine_the_agent_loop():
    """Named separately so the failure message points at the file that regressed."""
    assert "AgentLoop" not in _agent_loop_definitions().get("services/gateway/main.py", [])
    main_src = (GATEWAY / "main.py").read_text(encoding="utf-8")
    assert "async def AgentLoop(" not in main_src
    # A max-iterations constant defined there would be equally dead.
    assert "MAX_TOOL_ITERATIONS" not in main_src


def test_every_caller_binds_to_the_real_loop_via_agent_loop_module():
    """Importing the class from anywhere else would resurrect a copy."""
    callers = {
        "services/gateway/orchestrator.py": "from services.gateway.agent_loop import AgentLoop",
        "services/gateway/state_machine.py": "from services.gateway.agent_loop import AgentLoop",
    }
    for rel, expected in callers.items():
        assert expected in (REPO / rel).read_text(encoding="utf-8"), rel


# --- one set of agent limits ------------------------------------------------


def test_raven_max_total_seconds_agrees_across_all_three_sources():
    """agent_loop prefers the DB setting, falls back to gateway/config, and the
    orchestrator merges _DEFAULTS when the DB row is blank. If these three
    disagree the effective limit silently depends on deployment shape."""
    from_services_config = _assigned_int("services/config.py", "RAVEN_MAX_TOTAL_SECONDS")
    from_orchestrator = _dict_item_str(
        "services/gateway/orchestrator.py", "_DEFAULTS", "raven_max_total_seconds"
    )
    from_seed = _seeded_settings().get("raven_max_total_seconds")

    assert from_services_config is not None
    assert from_orchestrator is not None
    assert from_seed is not None

    assert int(from_orchestrator) == from_services_config, (
        "orchestrator._DEFAULTS only applies when the Identity row is missing, so it "
        "must equal the seeded value or a blanked row silently changes the limit"
    )
    assert int(from_seed) == from_services_config, (
        "the Identity seed wins at runtime, so it is the effective default"
    )


def test_the_raven_timeout_is_enabled_and_non_trivial():
    """It was 0 in services/config.py, which reads as 'no limit' while every
    other source said 1800."""
    value = _assigned_int("services/config.py", "RAVEN_MAX_TOTAL_SECONDS")
    assert value is not None and value > 0
    # Long enough for a real multi-step mission, short enough to stop a looping one.
    assert 600 <= value <= 7200, value


def test_the_iteration_ceiling_is_seeded_so_the_override_is_discoverable():
    """agent_loop reads `raven_max_iterations` from settings but nothing seeded
    it, so the documented override had no Settings row to live in."""
    seeds = _seeded_settings()
    assert "raven_max_iterations" in seeds, (
        "add a raven_max_iterations row to the Identity seed, or drop the claim that "
        "the ceiling is overridable per deployment"
    )
    assert int(seeds["raven_max_iterations"]) == 60, (
        "the ceiling is asserted at 60 in test_the_live_iteration_ceiling_is_sixty; "
        "change both in one commit"
    )


def test_the_live_iteration_ceiling_is_sixty():
    """The number both ADRs used to get wrong (they said 30, from the dead copy)."""
    tree = _tree("services/gateway/agent_loop.py")
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(isinstance(t, ast.Name) and t.id == "MAX_TOOL_ITERATIONS" for t in targets):
            assert isinstance(node.value, ast.Constant)
            assert node.value.value == 60, (
                "if this ceiling really changed, update docs/adr_007 and "
                "docs/adr_013 in the same commit"
            )
            return
    pytest.fail("MAX_TOOL_ITERATIONS is no longer assigned in agent_loop.py")


def test_the_docs_no_longer_quote_the_dead_numbers():
    """Guards the specific regression: docs were written from a code path that
    nothing executed. The superseded values are allowed to appear once per file,
    inside the block-quoted correction note that explains where they came from —
    that note is the whole point, so stripping it would lose the history."""
    stale_numbers = ("MAX_TOOL_ITERATIONS = 30", "RAVEN_MAX_TOTAL_SECONDS = 600")
    for rel in ("docs/adr_007_hard_timeout.md", "docs/adr_013_agentloop_termination_safeguards.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        # Drop block-quoted lines: the correction note is allowed to name the
        # superseded values, everything else is the ADR speaking in the present tense.
        body = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(">")
        )
        for stale in stale_numbers:
            assert stale not in body, (rel, stale, "still asserted as current")
        assert "MAX_TOOL_ITERATIONS = 60" in body, rel
        assert "RAVEN_MAX_TOTAL_SECONDS" in body and "1800" in body, rel
