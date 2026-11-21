"""The routing rule: anything near secrets, household data or production stays local."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from services.gateway.trainer.teacher_routing import route_task

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "task",
    [
        "Refactor the pager so page size is configurable, with tests",
        "Fix the TypeScript error in the workspace file tree component",
        "Explain why this pytest fixture leaks an event loop",
    ],
)
def test_public_code_reasoning_may_go_to_a_teacher(task):
    assert route_task(task)["route"] == "cloud"


@pytest.mark.parametrize(
    ("task", "reason"),
    [
        ("Rotate the leaked OpenRouter keys", "secrets"),
        ("Decrypt the stored integration tokens to debug a 401", "secrets"),
        ("Why does the family calendar show the wrong events", "household"),
        ("Find where the kids' screen time is computed", "household"),
        ("Restore the identity backup on the production server", "production"),
        ("ssh admin@hub and check the container logs", "production"),
    ],
)
def test_sensitive_tasks_stay_local(task, reason):
    decision = route_task(task)

    assert decision["route"] == "local"
    assert any(reason in r for r in decision["reasons"])


def test_paths_and_names_decide_too():
    assert route_task("Tidy this module", ["services/identity/models.py"])["route"] == "local"
    assert route_task("Tidy this module", [".env"])["route"] == "local"
    assert route_task("Document the settings", [".env.example"])["route"] == "cloud"
    assert route_task("Help Zebulonia with the build", names=["Zebulonia"])["route"] == "local"


def test_the_cli_exit_code_is_the_decision(capsys):
    spec = importlib.util.spec_from_file_location("teacher_route", ROOT / "scripts" / "teacher_route.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["Refactor the pager tests", "--names-file", "/nonexistent"]) == 0
    assert module.main(["Rotate the API keys", "--json", "--names-file", "/nonexistent"]) == 1
    assert '"route": "local"' in capsys.readouterr().out
