"""Every registry that decides whether the assistant can use the home's to-do lists.

The same surfaces as ``test_chore_registry_rows.py``: the allowlist, the
dispatch map, the single-turn map, the Raven table, the guide and the
capability index. Missing any one makes "add milk to the shopping list" fail
in its own way.
"""
from __future__ import annotations

import re

from services.execution import schemas as execution_schemas
from services.execution.schemas import TodoListRequest
from services.gateway.agent_loop import ALLOWED_TOOLS
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS
from services.gateway.tests.test_meal_plan_registry_rows import GUIDE, _agent_loop_source, _schema_map
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE, SVC_EXECUTION


def test_allowed_dispatched_and_reachable_from_one_turn():
    assert "todolistrequest" in ALLOWED_TOOLS
    assert '"todolistrequest": (EXECUTION_SVC, "/execute/todo")' in _agent_loop_source()
    assert SINGLE_TURN_TOOL_ENDPOINTS.get("todolistrequest") == "/execute/todo"


def test_the_raven_row_and_its_hint_match_the_schema():
    row = next((row for row in _RAVEN_TOOL_TABLE if row[0] == "TodoListRequest"), None)
    assert row is not None
    _name, service, method, path, requires_workspace, description, hint = row
    assert (service, method, path, requires_workspace) == (SVC_EXECUTION, "POST", "/execute/todo", False)
    assert "household member" in description
    match = re.search(r"payload fields: (.*?)\.", hint)
    hinted = {t.strip() for t in match.group(1).split(",") if t.strip()}
    assert hinted and not hinted - set(TodoListRequest.model_fields), sorted(hinted - set(TodoListRequest.model_fields))


def test_the_guide_and_index_teach_it():
    start = GUIDE.index("### TodoListRequest")
    section = GUIDE[start:GUIDE.find("\n### ", start + 1)]
    for field in ("`list_name`", "`item`", "`items`", "`action`"):
        assert field in section
    assert "Never pick one for them" in section
    assert "to-do lists" in _schema_map()["TodoListRequest"]
    assert hasattr(execution_schemas, "TodoListRequest")
