"""Tests for the workspace composer's explicit execution modes.

The workspace composer used to have exactly one behaviour: every message became
a Raven mission. That made the composer useless for the two other things people
actually want in a workspace -- asking a grounded question, and getting one job
done -- and it meant the retrieval path built for the Librarian was unreachable
from a workspace at all.

These tests pin the routing, and in particular pin the parts that are easy to
regress silently: that a single task retrieves nothing, that a Raven dispatch
carries the context it was launched with, and that a mode the client named is
never second-guessed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import services.gateway.orchestrator as orchestrator
from services.gateway import main as gateway_main
from services.gateway.main import app, _resolve_workspace_ask_mode

client = TestClient(app)

CREDS = {"user": "testuser", "is_admin": False}


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def asks(monkeypatch):
    """Record what each execution path was called with.

    Returns the dict the tests assert against. Every collaborator the endpoint
    touches is replaced, so a test failure names the routing decision that broke
    rather than a socket error three layers down.
    """
    seen: dict = {"context_calls": [], "inference_calls": [], "mission_calls": []}

    async def fake_context(query, user_id, creds=None, workspace_id=None, include_curriculum=True, include_library=False):
        seen["context_calls"].append({
            "query": query,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "include_curriculum": include_curriculum,
            "include_library": include_library,
        })
        return "RETRIEVED CONTEXT"

    async def fake_inference(**kwargs):
        seen["inference_calls"].append(kwargs)
        return "THE ANSWER"

    async def fake_mission(**kwargs):
        seen["mission_calls"].append(kwargs)
        return {"id": 4242, "status": "queued"}

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_context)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(gateway_main, "_enqueue_user_mission", fake_mission)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", AsyncMock(return_value=dict(CREDS)))
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "SYSTEM")
    monkeypatch.setattr(gateway_main, "get_librarian_model", AsyncMock(return_value="lib-model"))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="assistant-model"))
    monkeypatch.setattr(gateway_main, "_build_raven_system_prompt", AsyncMock(return_value="RAVEN SYSTEM"))
    monkeypatch.setattr(gateway_main, "_workspace_file_overview", AsyncMock(return_value=""))
    return seen


def _post(auth_headers, **body):
    return client.post("/api/workspaces/ws-1/ask", json=body, headers=auth_headers)


def test_an_explicit_mode_is_never_second_guessed():
    """Naming a mode is the escape hatch; auto may not overrule it.

    This is the regression guard for the recorded incident at
    ``orchestrator.py``: a workspace-creating prompt lacking the literal word
    "raven" was routed to the single-turn path, which cannot create workspaces,
    and answered 404. Auto is allowed to be wrong about an *auto* request. It is
    not allowed to overrule a request that said what it wanted.
    """
    for mode in ("librarian", "single_task", "raven"):
        resolved, reason = _resolve_workspace_ask_mode("create a workspace and deploy it", mode)
        assert resolved == mode
        assert reason == "chosen explicitly"


def test_a_raven_prompt_resolves_to_raven_under_auto():
    resolved, reason = _resolve_workspace_ask_mode("raven refactor the parser", "auto")
    assert resolved == "raven"
    assert "Raven" in reason


def test_a_question_resolves_to_the_librarian_under_auto():
    for query in ("what did Macduff say about poverty?", "who wrote the Faithful Promiser"):
        resolved, _ = _resolve_workspace_ask_mode(query, "auto")
        assert resolved == "librarian", query


def test_a_bare_command_resolves_to_a_single_task_under_auto():
    """A command is not a question, so it must not spend the retrieval budget."""
    resolved, _ = _resolve_workspace_ask_mode("rename the failing test", "auto")
    assert resolved == "single_task"


def test_a_workspace_cannot_reach_the_retrieval_path_without_asking(auth_headers, asks):
    response = _post(auth_headers, query="summarize this project's README", mode="single_task")
    assert response.status_code == 200
    assert asks["context_calls"] == [], "a single task must not retrieve"
    assert asks["inference_calls"][0]["rag_context"] == ""


def test_the_librarian_answers_from_retrieved_sources(auth_headers, asks):
    response = _post(auth_headers, query="anything", mode="librarian")
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "THE ANSWER"
    assert body["model"] == "lib-model"
    assert asks["inference_calls"][0]["rag_context"] == "RETRIEVED CONTEXT"


def test_retrieval_is_scoped_to_the_workspace_being_asked_about(auth_headers, asks):
    _post(auth_headers, query="what did he say?", mode="librarian")
    assert asks["context_calls"][0]["workspace_id"] == "ws-1"
    assert asks["context_calls"][0]["user_id"] == "testuser"


def test_a_librarian_turn_asks_for_no_curriculum(auth_headers, asks):
    """Regression: a cited answer used to come back as an Ollama context error.

    ``TOTAL_CHARS_LIMIT`` bounds only the search hits, while the protocol
    lessons, CLI toolchain inventory and Nextcloud/HA inventories are appended
    after that budget is spent. A library question assembled 26,434 characters
    against an 8,192-token window and the model returned ``exceeds the
    available context size`` *as the answer* -- an error wearing the costume of
    a response. A read-only turn cannot act on any of that furniture, so it must
    not be handed it.
    """
    response = _post(auth_headers, query="what did Macduff say about poverty?", mode="librarian")
    assert response.status_code == 200
    assert asks["context_calls"][0]["include_curriculum"] is False


def test_a_raven_dispatch_still_asks_for_the_curriculum(auth_headers, asks):
    """The other half: Raven is the caller the curriculum was written for.

    It creates workspaces, runs shell commands and dispatches tasks, so dropping
    its protocol lessons would be a silent capability regression. The flag has to
    be opt-out per caller, not global.
    """
    response = _post(auth_headers, query="summarize the project and fix it", mode="raven")
    assert response.status_code == 200
    assert asks["context_calls"][0]["include_curriculum"] is True


def test_a_raven_dispatch_carries_the_context_it_was_launched_with(auth_headers, asks):
    """The mixing the composer was missing.

    Raven already accepts a ``rag_context`` and injects it into its system
    prompt. The mission path simply never populated it, so a mission briefed
    from a book started holding nothing. Asserted on the dispatch call because
    that is the only place the omission lived.
    """
    response = _post(auth_headers, query="summarize the whole project and fix it", mode="raven")
    assert response.status_code == 200
    body = response.json()
    assert body["mission_id"] == 4242
    assert asks["mission_calls"][0]["rag_context"] == "RETRIEVED CONTEXT"
    assert asks["mission_calls"][0]["workspace_id"] == "ws-1"


def test_a_raven_dispatch_is_a_mission_and_not_a_synchronous_answer(auth_headers, asks):
    """A mission outlives the turn, so it must not be answered inline.

    ``_single_turn_inference`` is three steps at temperature 0. It cannot wait
    for a queued mission, which is exactly why the Librarian is not given a tool
    to call Raven. Asserted so that shape cannot be quietly changed back.
    """
    _post(auth_headers, query="deploy it", mode="raven")
    assert asks["inference_calls"] == []
    assert len(asks["mission_calls"]) == 1


def test_the_resolved_mode_and_its_reason_come_back_for_the_ui(auth_headers, asks):
    """The reason is returned so the composer can label its own guess.

    A silent guess that is wrong costs far more to diagnose than a chip the
    user can read and override.
    """
    body = _post(auth_headers, query="what does this do?", mode="auto").json()
    assert body["requested_mode"] == "auto"
    assert body["resolved_mode"] == "librarian"
    assert body["reason"]


def test_an_unknown_mode_is_refused_rather_than_defaulted(auth_headers, asks):
    """A typo must not quietly become whichever mode happened to be first."""
    response = _post(auth_headers, query="hello", mode="librraian")
    assert response.status_code == 422
    assert "librarian" in response.json()["detail"]


def test_a_blank_query_is_refused(auth_headers, asks):
    assert _post(auth_headers, query="   ", mode="librarian").status_code == 400
    assert _post(auth_headers, mode="librarian").status_code == 400


def test_asking_requires_an_identity(auth_headers, asks, monkeypatch):
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", AsyncMock(return_value=None))
    response = _post(auth_headers, query="what did he say?", mode="librarian")
    assert response.status_code == 401
    assert asks["inference_calls"] == []


def test_a_single_task_does_not_inherit_the_librarian_model(auth_headers, asks):
    """The two synchronous modes are different models on purpose.

    Librarian exists to read; the assistant model is the general talker. Sharing
    one would mean either paying the research model for chit-chat or starving
    research of the model that can actually do it.
    """
    _post(auth_headers, query="do the thing", mode="single_task")
    assert asks["inference_calls"][0]["model"] == "assistant-model"


def test_the_route_resolves_identity_for_real(auth_headers, asks, monkeypatch):
    """Exercise the real identity resolver rather than a stub.

    Every other test here replaces ``_resolve_identity_from_request`` wholesale,
    which would hide a break in the strict no-fallback path this route depends
    on -- the one where an unrecognised key is refused instead of being silently
    upgraded to the administrator, which would hand the caller another family's
    retrieved books.

    The strict check is stubbed rather than the two HTTP clients behind it, so
    the real ``_require_identity_from_request`` logic still runs: key presented,
    key rejected, 401.
    """
    monkeypatch.undo()
    monkeypatch.setattr(
        gateway_main, "_resolve_strict_identity", AsyncMock(return_value=None)
    )

    response = client.post(
        "/api/workspaces/ws-1/ask", json={"query": "what did he say?", "mode": "librarian"},
        headers=auth_headers,
    )
    assert response.status_code == 401, "an unknown key must not reach the model"

def test_a_librarian_turn_searches_the_library_even_when_the_query_names_no_book_word(
    asks, auth_headers,
):
    """Asking a library a question by author name is the natural way to ask one.

    "What did Macduff say about trusting God in poverty" contains no word from
    LIBRARY_INTENT_SIGNALS, so the keyword gate alone would silently skip the
    50 indexed Macduff chapters and the model would answer from its own memory
    instead. The research mode is billed to the user as answering from their
    books, so it brings the library in regardless of wording.
    """
    r = client.post(
        "/api/workspaces/ws-1/ask",
        json={"query": "What did Macduff say about trusting God in poverty?", "mode": "librarian"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert asks["context_calls"][0]["include_library"] is True


def test_a_model_that_cannot_answer_is_reported_as_a_failure(auth_headers, asks, monkeypatch):
    """A failed turn must not arrive as a successful answer.

    ``_single_turn_inference`` used to return "I encountered an error while
    trying to generate a response" as its string, which this route shipped with
    HTTP 200 and a success toast -- so a composer that never answered looked
    like one answering nonsense. Nothing was wrong except that it never worked.

    The client timeout is the other half of the same failure: with the default
    15s axios timeout the question was aborted before the model replied at all.
    """

    async def refusing(**kwargs):
        raise orchestrator.InferenceUnavailable("Ollama HTTP 503: queue_timeout")

    monkeypatch.setattr(orchestrator, "_single_turn_inference", refusing)

    response = _post(auth_headers, query="what did he say?", mode="librarian")

    assert response.status_code == 503
    assert "queue_timeout" in response.json()["detail"]


def test_a_raven_dispatch_does_not_force_the_library(asks, auth_headers):
    """Only the research mode promises the user's books; a mission does not.

    Without this, forcing the library in for missions would put the largest
    collection in front of every autonomous turn, which is the noise the
    keyword gate exists to prevent.
    """
    client.post(
        "/api/workspaces/ws-1/ask",
        json={"query": "Refactor the parser", "mode": "raven"},
        headers=auth_headers,
    )
    assert asks["context_calls"][0]["include_library"] is False
