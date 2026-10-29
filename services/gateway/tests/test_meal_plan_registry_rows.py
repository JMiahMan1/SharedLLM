"""Every registry that decides whether the assistant can plan a meal.

Five places must agree for a tool to work, and each fails differently when it
is missed: without ``ALLOWED_TOOLS`` the name falls through to the fuzzy
matcher and is rewritten to a *different* tool; without ``action_map`` the call
has nowhere to dispatch; without the Raven table the model is never told the
tool exists; without the single-turn map the chat path cannot reach it; and
without a schema the payload hint has nothing to validate against. One file
pins the surfaces the meal-plan write action needs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from services.execution import schemas as execution_schemas
from services.execution.schemas import MealPlanRequest
from services.gateway.agent_loop import ALLOWED_TOOLS
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE, SVC_EXECUTION

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "prompts" / "single_turn_tool_guide.md").read_text(encoding="utf-8")
INDEX_SCRIPT = ROOT / "scripts" / "index_capabilities.py"


def _agent_loop_source() -> str:
    return (GATEWAY / "agent_loop.py").read_text(encoding="utf-8")


def _schema_map() -> dict:
    """Literal-eval the capability script's schema_map without running it."""
    tree = ast.parse(INDEX_SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "schema_map" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("index_capabilities.py no longer defines schema_map")


def test_allowlist_admits_the_meal_plan_tool():
    assert "mealplanrequest" in ALLOWED_TOOLS


def test_action_map_dispatches_to_the_meal_plan_route():
    assert '"mealplanrequest": (EXECUTION_SVC, "/execute/meal_plan")' in _agent_loop_source()


def test_the_single_turn_path_can_reach_it():
    assert SINGLE_TURN_TOOL_ENDPOINTS.get("mealplanrequest") == "/execute/meal_plan"


def test_the_raven_table_row_points_at_execution():
    row = next((row for row in _RAVEN_TOOL_TABLE if row[0] == "MealPlanRequest"), None)
    assert row is not None, "MealPlanRequest is missing from _RAVEN_TOOL_TABLE"
    _name, service, method, path, requires_workspace, description, hint = row
    assert service == SVC_EXECUTION
    assert method == "POST"
    assert path == "/execute/meal_plan"
    assert requires_workspace is False
    assert "meal plan" in description.lower()
    assert "payload fields:" in hint


def test_the_payload_hint_names_fields_the_schema_actually_has():
    row = next(row for row in _RAVEN_TOOL_TABLE if row[0] == "MealPlanRequest")
    match = re.search(r"payload fields: (.*?)\.", row[6])
    assert match, f"unparseable payload hint: {row[6]!r}"
    hinted = {token.strip(" .,") for token in match.group(1).split(",") if token.strip(" .,")}
    missing = hinted - set(MealPlanRequest.model_fields)
    assert not missing, f"hint promises fields the schema lacks: {sorted(missing)}"


def test_the_single_turn_guide_teaches_the_tool():
    start = GUIDE.index("### MealPlanRequest")
    end = GUIDE.find("\n### ", start + 1)
    section = GUIDE[start:end]
    for field in ("`text`", "`day`", "`recipe`"):
        assert field in section, f"the guide never names the {field} field"
    assert "week plan" in section.lower()
    assert "never overwritten" in section


def test_the_capability_index_advertises_it():
    entry = _schema_map()["MealPlanRequest"]
    assert "Recipes/Meal plan.md" in entry
    assert "never overwritten" in entry
    assert hasattr(execution_schemas, "MealPlanRequest"), "the schema_map key has no schema"
