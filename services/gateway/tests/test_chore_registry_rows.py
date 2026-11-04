"""Every registry that decides whether the assistant can manage chores.

The same five surfaces as ``test_meal_plan_registry_rows.py``: the allowlist,
the dispatch map, the single-turn map, the Raven table and the guide, plus the
capability index. Missing any one makes "give Noah a chore" fail in its own way.
"""
from __future__ import annotations

import re

from services.execution import schemas as execution_schemas
from services.execution.schemas import ChoreRequest
from services.gateway.agent_loop import ALLOWED_TOOLS
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS
from services.gateway.tests.test_meal_plan_registry_rows import GUIDE, _agent_loop_source, _schema_map
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE, SVC_EXECUTION


def test_allowed_dispatched_and_reachable_from_one_turn():
    assert "chorerequest" in ALLOWED_TOOLS
    assert '"chorerequest": (EXECUTION_SVC, "/execute/chores")' in _agent_loop_source()
    assert SINGLE_TURN_TOOL_ENDPOINTS.get("chorerequest") == "/execute/chores"


def test_the_raven_row_and_its_hint_match_the_schema():
    row = next((row for row in _RAVEN_TOOL_TABLE if row[0] == "ChoreRequest"), None)
    assert row is not None
    _name, service, method, path, requires_workspace, description, hint = row
    assert (service, method, path, requires_workspace) == (SVC_EXECUTION, "POST", "/execute/chores", False)
    assert "parents" in description
    match = re.search(r"payload fields: (.*?)\.", hint)
    hinted = {t.strip() for t in match.group(1).split(",") if t.strip()}
    assert not hinted - set(ChoreRequest.model_fields), sorted(hinted - set(ChoreRequest.model_fields))


def test_the_guide_and_index_teach_it():
    start = GUIDE.index("### ChoreRequest")
    section = GUIDE[start:GUIDE.find("\n### ", start + 1)]
    for field in ("`title`", "`assignee`", "`repeat`", "`scope`"):
        assert field in section
    assert "never from a voice alone" in section
    assert "never from a voice alone" in _schema_map()["ChoreRequest"]
    assert hasattr(execution_schemas, "ChoreRequest")
