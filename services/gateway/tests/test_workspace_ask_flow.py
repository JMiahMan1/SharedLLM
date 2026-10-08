"""The workspace composer's answer must be an answer.

Observed live: "can you transcribe the mkv?" in the Sermon workspace came back
as "Found 25 matches for 'sermon'". Five things lined up to produce that: the
request was routed as a question (it ends in "?"), the model never saw the
workspace's file names, the search tool handed it only a match count, the turn
was not pinned to the workspace it was asked from, and when the three tool
calls ran out the last tool's status line was returned as the answer. Each is
pinned here.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import services.gateway.orchestrator as orchestrator
from services.execution.handlers.workspace import _matching_file_names, _workspace_relative
from services.gateway import main as gateway_main
from services.gateway.main import _resolve_workspace_ask_mode, app
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE, SINGLE_TURN_EXCLUDED, tool_catalog

GUIDE = "Tools: WorkspaceSearchRequest, WorkspaceFileReadRequest."


# --- routing ----------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "can you transcribe the mkv file?",
    "could you please rename the wav file?",
    "please convert the sermon audio to mp3",
    "Can you make an outline from the study notes?",
])
def test_a_polite_request_to_do_something_is_a_task(query):
    resolved, reason = _resolve_workspace_ask_mode(query, "auto")
    assert resolved == "single_task", query
    assert "files in view" in reason


@pytest.mark.parametrize("query", [
    "can you explain what Joel 2:13 means?",
    "could you summarize the repentance notes?",
    "can you find where Jubilees is quoted?",
])
def test_a_polite_request_to_explain_is_still_a_question(query):
    resolved, _ = _resolve_workspace_ask_mode(query, "auto")
    assert resolved == "librarian", query


def test_a_plain_question_is_unchanged():
    assert _resolve_workspace_ask_mode("can the librarian cite Keener?", "auto")[0] == "librarian"
    assert _resolve_workspace_ask_mode("what is shuv?", "auto")[0] == "librarian"


# --- the ask endpoint hands over the workspace -------------------------------

def test_the_turn_is_pinned_to_the_workspace_and_shown_its_files(monkeypatch):
    seen: dict = {}

    async def fake_inference(**kwargs):
        seen.update(kwargs)
        return "THE ANSWER"

    overview = AsyncMock(return_value="FILES: sermon.mkv")
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(gateway_main, "_workspace_file_overview", overview)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", AsyncMock(return_value={"user": "u", "is_admin": False}))
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "SYSTEM")
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="assistant-model"))

    response = TestClient(app).post(
        "/api/workspaces/sermon/ask",
        json={"query": "can you transcribe the mkv?", "mode": "auto"},
        headers={"Authorization": "Bearer t"},
    )

    assert response.status_code == 200
    assert response.json()["resolved_mode"] == "single_task"
    assert seen["workspace_id"] == "sermon"
    assert seen["workspace_context"].startswith("FILES: sermon.mkv")
    assert "STTRequest" in seen["workspace_context"], "a task is shown the tools it may call"
    assert overview.await_args.args[0] == "sermon"


# --- the inference loop -------------------------------------------------------

def _scripted(monkeypatch, replies, tool_result="Found 25 file names and 0 lines matching 'sermon'"):
    replies = iter(replies)
    seen_messages: list[list[dict]] = []
    calls: list[dict] = []

    async def _call_ollama(payload, **kwargs):
        seen_messages.append(payload["messages"])
        return {"message": {"content": next(replies)}}

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append({"action": action, "workspace_id": workspace_id})
        return tool_result

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)
    return seen_messages, calls


async def _run(**overrides):
    kwargs = dict(
        query="can you transcribe the mkv?",
        model="m",
        system_prompt="SYSTEM",
        rag_context="",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="u"),
    )
    kwargs.update(overrides)
    return await orchestrator._single_turn_inference(**kwargs)


SEARCH = '{"tool": "WorkspaceSearchRequest", "query": "sermon"}'


@pytest.mark.asyncio
async def test_a_spent_tool_budget_ends_in_an_answer_not_a_tool_status(monkeypatch):
    seen, calls = _scripted(monkeypatch, [SEARCH, SEARCH, SEARCH, "The video is sermon.mkv; transcribing it needs Raven."])

    answer = await _run(workspace_id="sermon")

    assert answer == "The video is sermon.mkv; transcribing it needs Raven."
    assert len(calls) == 3
    assert "no more tools will run" in seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_a_closing_turn_that_will_not_conclude_says_so(monkeypatch):
    _scripted(monkeypatch, [SEARCH, SEARCH, SEARCH, SEARCH])

    answer = await _run()

    assert answer.startswith("I ran out of steps")
    assert "Found 25 file names" in answer


@pytest.mark.asyncio
async def test_every_tool_call_carries_the_workspace(monkeypatch):
    _, calls = _scripted(monkeypatch, [SEARCH, "done"])

    await _run(workspace_id="sermon")

    assert calls == [{"action": "workspacesearchrequest", "workspace_id": "sermon"}]


@pytest.mark.asyncio
async def test_the_workspace_files_reach_the_system_prompt(monkeypatch):
    seen, _ = _scripted(monkeypatch, ["An answer."])

    await _run(workspace_id="sermon", workspace_context="- Journey of Grace Service.mkv (348.7 MB)")

    system = seen[0][0]["content"]
    assert "Journey of Grace Service.mkv" in system
    assert "suggest sending it to Raven" in system


@pytest.mark.asyncio
async def test_the_executor_overrides_the_workspace_the_model_guessed():
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "SUCCESS", "message": "ok", "detail": {"matches": [], "files": []}})
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.object(orchestrator, "get_all_settings", AsyncMock(return_value={"execution_svc_url": "http://exec"})), \
         patch("services.gateway.main.shared_http_client") as ctx:
        ctx.return_value.__aenter__.return_value = mock_client
        await orchestrator._execute_single_tool(
            "workspacesearchrequest",
            {"action": "workspacesearchrequest", "payload": {"query": "mkv", "workspace_id": "default"}},
            "transcribe the mkv",
            orchestrator.ResolvedCredentials(user="u"),
            workspace_id="sermon",
        )

    assert mock_client.post.call_args.kwargs["json"]["workspace_id"] == "sermon"


def test_a_search_result_shows_its_hits():
    text = orchestrator._format_workspace_search({
        "message": "Found 1 file names and 1 lines matching 'mkv'",
        "detail": {
            "files": ["Journey of Grace Service.mkv"],
            "matches": [{"path": "transcribe.py", "line": 4, "text": "SRC = 'service.mkv'"}],
        },
    })
    assert "- Journey of Grace Service.mkv" in text
    assert "- transcribe.py:4: SRC = 'service.mkv'" in text


# --- the search handler ---------------------------------------------------------

def test_file_names_are_searchable(tmp_path):
    (tmp_path / "Bible Study").mkdir()
    (tmp_path / "Bible Study" / "notes.md").write_text("x")
    (tmp_path / "Journey of Grace [7bu].mkv").write_bytes(b"")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "a.mkv").write_bytes(b"")

    assert _matching_file_names(str(tmp_path), str(tmp_path), "mkv") == ["Journey of Grace [7bu].mkv"]
    assert _matching_file_names(str(tmp_path), str(tmp_path), "NOTES") == ["Bible Study/notes.md"]


def test_a_hit_path_is_relative_to_the_workspace_not_the_process(tmp_path):
    sub = tmp_path / "Bible Study"
    assert _workspace_relative("./notes.md", str(sub), str(tmp_path)) == "Bible Study/notes.md"
    assert _workspace_relative(str(sub / "notes.md"), str(sub), str(tmp_path)) == "Bible Study/notes.md"


# --- one tool table ---------------------------------------------------------------


def test_every_registry_tool_is_reachable_from_a_single_turn():
    """The single-turn table is generated, so it cannot fall behind again."""
    for name, service, _method, path, *_ in _RAVEN_TOOL_TABLE:
        if name in SINGLE_TURN_EXCLUDED:
            assert name.lower() not in orchestrator.SINGLE_TURN_TOOL_ENDPOINTS
            continue
        assert orchestrator.SINGLE_TURN_TOOL_ENDPOINTS.get(name.lower()) == path, name
        expected_key = orchestrator._SERVICE_SETTINGS_KEY[service]
        assert orchestrator._TOOL_SERVICE_MAP.get(name.lower(), "execution_svc_url") == expected_key, name


def test_the_routes_that_used_to_be_wrong_are_right():
    assert orchestrator.SINGLE_TURN_TOOL_ENDPOINTS["llminforequest"] == "/execute/llm/info"
    assert orchestrator._TOOL_SERVICE_MAP["storageindexrequest"] == "storage_svc_url"
    for name in ("sttrequest", "ocrrequest", "podcastrenderrequest", "imageeditrequest", "ghrequest"):
        assert name in orchestrator.SINGLE_TURN_TOOL_ENDPOINTS, name


def test_every_single_turn_route_is_served():
    """A row whose path its service does not serve is a 404 waiting to happen.

    Found three when this was written: the network scan, storage TTS (wrong
    service and path) and the LLM-info path.
    """
    import re
    from pathlib import Path

    # Read the decorators rather than importing the apps: the execution service
    # pulls in hardware clients the gateway's test environment does not carry.
    services = Path(__file__).resolve().parents[2]
    service_dir = {
        "execution_svc_url": "execution", "storage_svc_url": "storage", "rag_svc_url": "rag",
        "workspace_runtime_svc_url": "workspace_runtime", "identity_svc_url": "identity",
    }
    served: dict[str, set[str]] = {}
    for key, folder in service_dir.items():
        source = (services / folder / "main.py").read_text()
        served[key] = set(re.findall(r'@app\.(?:post|api_route)\("([^"]+)"', source))
    for name, path in orchestrator.SINGLE_TURN_TOOL_ENDPOINTS.items():
        key = orchestrator._TOOL_SERVICE_MAP.get(name, "execution_svc_url")
        assert path in served[key], f"{name} -> {key}{path} is not served"


def test_the_catalog_lists_what_dispatches_and_nothing_excluded():
    catalog = tool_catalog()
    assert "- STTRequest:" in catalog
    assert "- OcrRequest:" in catalog
    for name in SINGLE_TURN_EXCLUDED:
        assert f"- {name}:" not in catalog


async def _execute_capturing(action, payload, workspace_id, result):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=result)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    with patch.object(orchestrator, "get_all_settings", AsyncMock(return_value={})), \
         patch("services.gateway.main.shared_http_client") as ctx:
        ctx.return_value.__aenter__.return_value = mock_client
        text = await orchestrator._execute_single_tool(
            action, {"action": action, "payload": payload}, "q",
            orchestrator.ResolvedCredentials(user="u"), workspace_id=workspace_id,
        )
    return text, mock_client.post.call_args


@pytest.mark.asyncio
async def test_a_transcription_runs_in_the_workspace_with_time_to_finish():
    text, call = await _execute_capturing(
        "sttrequest", {"file_path": "talk.wav"}, "sermon",
        {"status": "SUCCESS", "message": "Transcribed talk.wav (11 chars)", "detail": {"transcript": "Rend hearts"}},
    )
    assert call.kwargs["json"]["workspace_id"] == "sermon"
    assert call.kwargs["timeout"].total == 600.0
    assert "Rend hearts" in text, "the transcript must reach the model, not just the status line"


@pytest.mark.asyncio
async def test_creating_a_workspace_from_a_workspace_keeps_the_new_id():
    _, call = await _execute_capturing(
        "workspacecreaterequest", {"name": "Advent Series"}, "sermon",
        {"id": "advent-series", "display_name": "Advent Series"},
    )
    assert call.kwargs["json"]["id"] == "advent-series"


@pytest.mark.asyncio
async def test_outside_a_workspace_a_result_stays_a_short_message():
    text, _ = await _execute_capturing(
        "noterequest", {"title": "x", "content": "y", "action": "create"}, None,
        {"status": "SUCCESS", "message": "Note saved.", "detail": {"id": 7}},
    )
    assert text == "Note saved."
