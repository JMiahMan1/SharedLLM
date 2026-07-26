"""Tests for the code_search execution handler."""

from unittest.mock import AsyncMock

import pytest

from services.execution.handlers.code_search import (
    SEARCH_SCRIPT,
    handle_code_search,
)
from services.execution.schemas import (
    CodeSearchRequest,
    UserContext,
)

BASE_URL = "http://ha.local"


@pytest.fixture
def mock_user_context():
    return UserContext(
        user="testuser",
        ha_url=BASE_URL,
        ha_token="mock-token",
        mass_token_enc="mock-enc",
    )


@pytest.fixture
def sample_request(mock_user_context):
    return CodeSearchRequest(
        user_context=mock_user_context,
        query="def fetch_user",
    )


@pytest.fixture
def full_request(mock_user_context):
    return CodeSearchRequest(
        user_context=mock_user_context,
        query="class UserService",
        sources=["github"],
        language="python",
        owner="testorg",
        repo="testrepo",
        max_results=50,
        output_file="/tmp/results.json",
    )


def test_search_script_path_resolves():
    """Verify the code_search.py script path is valid."""
    assert SEARCH_SCRIPT.exists(), f"Code search script not found at {SEARCH_SCRIPT}"
    assert SEARCH_SCRIPT.suffix == ".py"


def test_code_search_request_schema(sample_request):
    """Verify the schema validates correctly."""
    assert sample_request.query == "def fetch_user"
    assert sample_request.sources == ["github", "gitlab"]
    assert sample_request.language is None


def test_code_search_request_defaults(mock_user_context):
    """Verify default values are correct."""
    req = CodeSearchRequest(
        user_context=mock_user_context,
        query="test",
    )
    assert req.sources == ["github", "gitlab"]
    assert req.language is None
    assert req.max_results == 20


def test_code_search_request_custom_sources(mock_user_context):
    """Verify custom sources work."""
    req = CodeSearchRequest(
        user_context=mock_user_context,
        query="test",
        sources=["github"],
    )
    assert req.sources == ["github"]


def test_code_search_request_all_fields(full_request):
    """Verify all fields are preserved."""
    assert full_request.query == "class UserService"
    assert full_request.sources == ["github"]
    assert full_request.language == "python"
    assert full_request.owner == "testorg"
    assert full_request.repo == "testrepo"
    assert full_request.max_results == 50
    assert full_request.output_file == "/tmp/results.json"


@pytest.mark.asyncio
async def test_handle_code_search_success(sample_request, mocker):
    """Verify successful search returns SUCCESS status."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(
            b"QUERY: def fetch_user\ngithub: found 12 results",
            b"",
        )
    )
    mock_proc.returncode = 0

    mock_create = AsyncMock(return_value=mock_proc)
    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch(
        "asyncio.wait_for",
        new_callable=lambda: AsyncMock(
            return_value=(b"QUERY: def fetch_user\n", b"")
        ),
    )

    result = await handle_code_search(sample_request)

    assert result.status == "SUCCESS"
    assert "def fetch_user" in result.message
    assert result.service == "code_search"


@pytest.mark.asyncio
async def test_handle_code_search_command_failure(sample_request, mocker):
    """Verify failed command returns FAILURE status."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"", b"gh cli not found")
    )
    mock_create.return_value.returncode = 1

    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    async def mock_wait_for(coro, timeout):
        stdout, stderr = await coro
        return stdout, stderr

    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_code_search(sample_request)

    assert result.status == "FAILURE"
    assert "gh cli not found" in result.message


@pytest.mark.asyncio
async def test_handle_code_search_timeout(sample_request, mocker):
    """Verify timeout returns FAILURE status."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock()
    mock_create.return_value.returncode = 0

    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    async def mock_wait_for(coro, timeout):
        raise TimeoutError("timed out")

    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_code_search(sample_request)

    assert result.status == "FAILURE"
    assert "timed out" in result.message


@pytest.mark.asyncio
async def test_handle_code_search_with_language(full_request, mocker):
    """Verify language filter is passed correctly."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_code_search(full_request)

    call_args = mock_create.call_args
    assert "--language" in call_args[0]
    assert "python" in call_args[0]


@pytest.mark.asyncio
async def test_handle_code_search_with_owner_repo(full_request, mocker):
    """Verify owner and repo filters are passed correctly."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_code_search(full_request)

    call_args = mock_create.call_args
    assert "--owner" in call_args[0]
    assert "testorg" in call_args[0]
    assert "--repo" in call_args[0]
    assert "testrepo" in call_args[0]


@pytest.mark.asyncio
async def test_handle_code_search_with_output_file(full_request, mocker, tmp_path):
    """Verify output_file argument is passed correctly."""
    output_file = str(tmp_path / "results.json")
    full_request.output_file = output_file

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_code_search(full_request)

    call_args = mock_create.call_args
    assert "--output" in call_args[0]
    assert output_file in call_args[0]


@pytest.mark.asyncio
async def test_handle_code_search_max_results(full_request, mocker):
    """Verify max_results is passed correctly."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_code_search(full_request)

    call_args = mock_create.call_args
    assert "--max-results" in call_args[0]
    assert "50" in call_args[0]


@pytest.mark.asyncio
async def test_handle_code_search_empty_output(sample_request, mocker):
    """Verify empty output returns SUCCESS with informational message."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"", b"")
    )
    mock_create.return_value.returncode = 0

    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    result = await handle_code_search(sample_request)

    assert result.status == "SUCCESS"
    assert "No output captured" in result.message


@pytest.mark.asyncio
async def test_handle_code_search_general_error(sample_request, mocker):
    """Verify general exceptions return FAILURE."""
    mock_create = AsyncMock(side_effect=RuntimeError("subprocess error"))
    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    result = await handle_code_search(sample_request)

    assert result.status == "FAILURE"
    assert "subprocess error" in result.message


@pytest.mark.asyncio
async def test_handle_code_search_with_gitlab_source(mock_user_context, mocker):
    """Verify gitlab source is passed correctly."""
    req = CodeSearchRequest(
        user_context=mock_user_context,
        query="import os",
        sources=["gitlab"],
    )

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_code_search(req)

    call_args = mock_create.call_args
    assert "--query" in call_args[0]
    assert "import os" in call_args[0]
