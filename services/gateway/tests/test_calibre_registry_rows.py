"""Every registry that decides whether Raven can call CalibreRequest.

Five places must agree for a tool to work, and each fails differently when it
is missed: without ``ALLOWED_TOOLS`` the name falls through to the fuzzy
matcher and gets rewritten to a *different* tool (the documented
ImageEditRequest -> climaterequest incident); without ``action_map`` the call
has nowhere to dispatch; without the Raven table the model is never told the
tool exists; without the single-turn map the Librarian path cannot reach it;
and without a ``tool_builder`` entry the builder scaffolds a duplicate Python
tool instead of routing to the real one. One file pins all five so the next
tool cannot be added half-way.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.execution.schemas import CalibreRequest
from services.gateway.agent_loop import ALLOWED_TOOLS
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS
from services.gateway.tool_builder import _TOOLS
from services.gateway.tool_registry import SVC_EXECUTION, _RAVEN_TOOL_TABLE

GATEWAY = Path(__file__).resolve().parents[1]


def _agent_loop_source() -> str:
    return (GATEWAY / "agent_loop.py").read_text(encoding="utf-8")


def test_allowlist_admits_the_calibre_tool():
    assert "calibrerequest" in ALLOWED_TOOLS


def test_action_map_dispatches_to_the_calibre_route():
    source = _agent_loop_source()
    assert '"calibrerequest": (EXECUTION_SVC, "/execute/calibre")' in source


def test_the_fuzzy_regex_falls_back_to_calibre():
    source = _agent_loop_source()
    assert "(r'.*calibre.*', \"calibrerequest\")" in source


def test_the_schema_error_listing_advertises_it():
    source = _agent_loop_source()
    assert '"audiobookshelfrequest", "calibrerequest",' in source


def test_the_single_turn_path_can_reach_it():
    assert SINGLE_TURN_TOOL_ENDPOINTS.get("calibrerequest") == "/execute/calibre"


def test_the_raven_table_row_points_at_execution():
    row = next(
        (row for row in _RAVEN_TOOL_TABLE if row[0] == "CalibreRequest"), None
    )
    assert row is not None, "CalibreRequest is missing from _RAVEN_TOOL_TABLE"
    _name, service, method, path, requires_workspace, description, hint = row
    assert service == SVC_EXECUTION
    assert method == "POST"
    assert path == "/execute/calibre"
    assert requires_workspace is False
    assert "read-only" in description
    assert "payload fields:" in hint


def test_the_payload_hint_names_fields_the_schema_actually_has():
    row = next(row for row in _RAVEN_TOOL_TABLE if row[0] == "CalibreRequest")
    match = re.search(r"payload fields: (.*)\.", row[6])
    assert match, f"unparseable payload hint: {row[6]!r}"
    hinted = {token.strip(" .,") for token in match.group(1).split(",") if token.strip(" .,")}
    missing = hinted - set(CalibreRequest.model_fields)
    assert not missing, f"hint promises fields the schema lacks: {sorted(missing)}"


def test_tool_builder_advertises_it_so_it_is_never_scaffolded():
    names = {tool.name for tool in _TOOLS}
    assert "CalibreRequest" in names
    assert "calibrerequest" in ALLOWED_TOOLS
