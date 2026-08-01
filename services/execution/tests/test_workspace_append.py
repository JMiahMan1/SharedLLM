"""Unit tests for workspace file write append semantics."""

from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.workspace import handle_workspace_write
from services.execution.schemas import UserContext, WorkspaceFileWriteRequest


def _req(tmp_path, path, content, append=False):
    return WorkspaceFileWriteRequest(
        user_context=UserContext(user="testuser"),
        path=path,
        content=content,
        append=append,
    )


@pytest.mark.asyncio
async def test_workspace_write_then_append(tmp_path):
    """append=True appends to an existing file instead of overwriting it."""
    mock_resolve = AsyncMock(return_value=(str(tmp_path), {}))
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r1 = await handle_workspace_write(_req(tmp_path, "mem.md", "## first\n"))
        assert r1.status == "SUCCESS"
        r2 = await handle_workspace_write(_req(tmp_path, "mem.md", "## second\n", append=True))
        assert r2.status == "SUCCESS"
        assert r2.detail.get("appended") is True
    content = (tmp_path / "mem.md").read_text()
    assert "first" in content and "second" in content
    assert content.index("first") < content.index("second")


@pytest.mark.asyncio
async def test_workspace_write_overwrites_without_append(tmp_path):
    """Without append, writes overwrite the file (default behavior)."""
    (tmp_path / "mem.md").write_text("OLD\n")
    mock_resolve = AsyncMock(return_value=(str(tmp_path), {}))
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_write(_req(tmp_path, "mem.md", "NEW\n"))
        assert r.status == "SUCCESS"
    assert (tmp_path / "mem.md").read_text() == "NEW\n"


@pytest.mark.asyncio
async def test_workspace_append_creates_file_when_missing(tmp_path):
    """append=True on a missing file creates it (no crash)."""
    mock_resolve = AsyncMock(return_value=(str(tmp_path), {}))
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_write(_req(tmp_path, "mem.md", "## first\n", append=True))
        assert r.status == "SUCCESS"
    assert (tmp_path / "mem.md").read_text() == "## first\n"
