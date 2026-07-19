"""Regression tests for autonomous-mission routing.

Root cause (open-ended mission misrouted): a job submitted through
POST /api/raven/missions carries a `_mission_id`, but routing decisions
(`_is_autonomous_job` in the worker and `is_autonomous` in the orchestrator)
were made purely from `is_raven_intent(query)`. An open-ended mission whose
prose lacked the literal "raven" keyword was misrouted to the single-turn
Librarian path, which cannot create workspaces -> WorkspaceCreateRequest
"not supported in the standard path" -> 502/404 workspace-not-found.

A `_mission_id` must force the autonomous path regardless of query text.
"""
from __future__ import annotations

from types import SimpleNamespace

from services.gateway.background_worker import RavenWorker
from services.gateway.intent_engine import is_raven_intent


def test_is_raven_intent_keywordless_prompt_is_false():
    # Baseline: an open-ended prompt without the "raven" keyword is NOT
    # classified as a Raven intent by the heuristic alone.
    q = "You have full autonomy. Choose a useful task and build it end to end."
    assert is_raven_intent(q) is False


def test_mission_id_forces_autonomous_even_without_keyword():
    fake_self = SimpleNamespace()
    payload = {
        "query": "You have full autonomy. Build something useful end to end.",
        "_mission_id": 1,
    }
    # Bound-method call; the method does not touch instance state.
    assert RavenWorker._is_autonomous_job(fake_self, payload, "default") is True


def test_no_mission_id_and_no_keyword_is_not_autonomous():
    fake_self = SimpleNamespace()
    payload = {"query": "what's the weather like today?"}
    assert RavenWorker._is_autonomous_job(fake_self, payload, "default") is False


def test_raven_keyword_command_still_autonomous():
    fake_self = SimpleNamespace()
    payload = {"query": "raven, fix the failing tests in the repo"}
    assert RavenWorker._is_autonomous_job(fake_self, payload, "default") is True
