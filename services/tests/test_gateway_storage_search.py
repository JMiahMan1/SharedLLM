"""Tests for gateway code orchestration storage search integration."""
import os

import pytest

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("INIT_DB", "false")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("IDENTITY_SVC_URL", "http://localhost:8001")
os.environ.setdefault("EXECUTION_SVC_URL", "http://localhost:8003")
os.environ.setdefault("RAG_SVC_URL", "http://localhost:8003")
os.environ.setdefault("SEARXNG_URL", "http://localhost:8080")



@pytest.fixture
def mock_http_client():
    class FakeResponse:
        def __init__(self, status_code=200, json_data=None):
            self.status_code = status_code
            self._json_data = json_data or {}

        def json(self):
            return self._json_data

    async def fake_request(method, url, json=None, headers=None, timeout=None):
        if "/files/list" in url:
            return FakeResponse(200, {
                "entries": [
                    {"path": "services/gateway/main.py", "is_dir": False, "size": 50000},
                    {"path": "services/execution/handlers/light.py", "is_dir": False, "size": 2000},
                    {"path": "README.md", "is_dir": False, "size": 1000},
                ]
            })
        return FakeResponse(200, {})

    return fake_request


@pytest.mark.asyncio
async def test_should_search_storage_for_code_query_detects_file_references():
    from services.gateway.main import should_search_storage_for_code_query

    assert should_search_storage_for_code_query("Fix the bug in gateway/main.py") is True
    assert should_search_storage_for_code_query("Update the light handler module") is True
    assert should_search_storage_for_code_query("Add a new .py file to services") is True
    assert should_search_storage_for_code_query("What is the weather?") is False


@pytest.mark.asyncio
async def test_should_search_storage_for_code_query_detects_path_references():
    from services.gateway.main import should_search_storage_for_code_query

    assert should_search_storage_for_code_query("Check the /config directory") is True
    assert should_search_storage_for_code_query("Look at the workspace folder") is True


@pytest.mark.asyncio
async def test_has_explicit_action_request_detects_commands():
    from services.gateway.main import has_explicit_action_request

    assert has_explicit_action_request("Turn on the living room light") is True
    assert has_explicit_action_request("Switch off the kitchen speaker") is True
    assert has_explicit_action_request("Play some music") is True
    assert has_explicit_action_request("Pause the media") is True
    assert has_explicit_action_request("What time is it?") is False


@pytest.mark.asyncio
async def test_extract_media_transport_command_parses_commands():
    from services.gateway.main import extract_media_transport_command

    assert extract_media_transport_command("Pause the music") == "pause"
    assert extract_media_transport_command("Resume playback") == "resume"
    assert extract_media_transport_command("Stop the video") == "stop"
    assert extract_media_transport_command("Skip to next track") == "next"
    assert extract_media_transport_command("Go back to previous") == "previous"
    assert extract_media_transport_command("What's the weather?") is None


@pytest.mark.asyncio
async def test_requests_status_followup_detects_followup_queries():
    from services.gateway.main import requests_status_followup

    assert requests_status_followup("Check again") is True
    assert requests_status_followup("Recheck the status") is True
    assert requests_status_followup("What's the status after that?") is True
    assert requests_status_followup("Try again afterwards") is True
    assert requests_status_followup("Turn on the light") is False
    assert requests_status_followup("What time is it?") is False
