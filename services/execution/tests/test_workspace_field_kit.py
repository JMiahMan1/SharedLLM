"""Tests for the Raven field kit workspace tools: patch, read, search, list, shell."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.workspace import (
    handle_workspace_list,
    handle_workspace_patch,
    handle_workspace_read,
    handle_workspace_search,
    handle_workspace_shell,
)
from services.execution.schemas import (
    ReplacementChunk,
    UserContext,
    WorkspaceFilePatchRequest,
    WorkspaceFileReadRequest,
    WorkspaceListRequest,
    WorkspaceSearchRequest,
    WorkspaceShellRequest,
)

# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_ws():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _user():
    return UserContext(user="testuser")


def _resolve(tmp_path, details=None):
    return AsyncMock(return_value=(str(tmp_path), details or {}))


def _patch_req(tmp_path, path, chunks, commit_after=False):
    return WorkspaceFilePatchRequest(
        user_context=_user(),
        path=path,
        chunks=chunks,
        commit_after=commit_after,
    )


def _read_req(tmp_path, path, offset_lines=0, limit_lines=300, line_numbers=True):
    # The agent's read tool asks for numbered lines; other callers get plain text.
    return WorkspaceFileReadRequest(
        user_context=_user(),
        path=path,
        offset_lines=offset_lines,
        limit_lines=limit_lines,
        line_numbers=line_numbers,
    )


def _search_req(tmp_path, query, path=".", include=None, exclude=None, glob=None, max_results=100):
    return WorkspaceSearchRequest(
        user_context=_user(),
        query=query,
        path=path,
        include=include,
        exclude=exclude,
        glob=glob,
        max_results=max_results,
    )


def _list_req(tmp_path, path=".", glob=None, max_entries=300):
    return WorkspaceListRequest(
        user_context=_user(),
        path=path,
        glob=glob,
        max_entries=max_entries,
    )


def _shell_req(command, cwd="."):
    return WorkspaceShellRequest(
        user_context=_user(),
        command=command,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Patch: exact matching, all-or-nothing, diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_exact_success(tmp_ws):
    """Exact old_text occurring once is replaced."""
    (tmp_ws / "f.txt").write_text("alpha\nbeta\ngamma\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="beta\n", new_text="BETA\n"),
        ]))
    assert r.status == "SUCCESS"
    assert (tmp_ws / "f.txt").read_text() == "alpha\nBETA\ngamma\n"
    diff = r.detail.get("diff", "")
    assert "beta" in diff and "BETA" in diff


@pytest.mark.asyncio
async def test_patch_not_found(tmp_ws):
    """Missing old_text fails the chunk with a not-found message."""
    (tmp_ws / "f.txt").write_text("alpha\nbeta\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="NONEXISTENT", new_text="X"),
        ]))
    assert r.status == "FAILURE"
    assert "not found" in r.message.lower()


@pytest.mark.asyncio
async def test_patch_multiple_matches(tmp_ws):
    """old_text found more than once fails unless replace_all is set."""
    (tmp_ws / "f.txt").write_text("x\ny\nx\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="x\n", new_text="X\n"),
        ]))
    assert r.status == "FAILURE"
    assert "found 2 times" in r.message or "multiple" in r.message.lower()


@pytest.mark.asyncio
async def test_patch_replace_all(tmp_ws):
    """replace_all=true replaces every occurrence."""
    (tmp_ws / "f.txt").write_text("x\ny\nx\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="x\n", new_text="X\n", replace_all=True),
        ]))
    assert r.status == "SUCCESS"
    assert (tmp_ws / "f.txt").read_text() == "X\ny\nX\n"


@pytest.mark.asyncio
async def test_patch_all_or_nothing(tmp_ws):
    """If any chunk fails validation, none are applied."""
    (tmp_ws / "f.txt").write_text("alpha\nbeta\ngamma\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="beta\n", new_text="BETA\n"),
            ReplacementChunk(old_text="NONEXISTENT", new_text="X"),
        ]))
    assert r.status == "FAILURE"
    # Original content must be untouched
    assert (tmp_ws / "f.txt").read_text() == "alpha\nbeta\ngamma\n"


@pytest.mark.asyncio
async def test_patch_diff_returned(tmp_ws):
    """Result detail contains a unified diff."""
    (tmp_ws / "f.txt").write_text("hello world\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="hello world\n", new_text="goodbye world\n"),
        ]))
    assert r.status == "SUCCESS"
    diff = r.detail.get("diff", "")
    assert "---" in diff or "+++" in diff or "@@" in diff


@pytest.mark.asyncio
async def test_patch_empty_old_text_appends(tmp_ws):
    """Empty old_text appends new_text to end of file."""
    (tmp_ws / "f.txt").write_text("existing\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_patch(_patch_req(tmp_ws, "f.txt", [
            ReplacementChunk(old_text="", new_text="appended\n"),
        ]))
    assert r.status == "SUCCESS"
    assert "appended" in (tmp_ws / "f.txt").read_text()


# ---------------------------------------------------------------------------
# Read: numbered lines, next_offset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_numbered_lines(tmp_ws):
    """Read returns lines numbered '   N| text'."""
    (tmp_ws / "f.txt").write_text("line one\nline two\nline three\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_read(_read_req(tmp_ws, "f.txt", offset_lines=1, limit_lines=3))
    assert r.status == "SUCCESS"
    content = r.detail["content"]
    assert "   1| line one" in content
    assert "   2| line two" in content
    assert "   3| line three" in content


@pytest.mark.asyncio
async def test_read_next_offset(tmp_ws):
    """next_offset is set when the file has more lines beyond the window."""
    (tmp_ws / "f.txt").write_text("\n".join(f"line{i}" for i in range(500)))
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_read(_read_req(tmp_ws, "f.txt", offset_lines=1, limit_lines=100))
    assert r.status == "SUCCESS"
    assert r.detail["next_offset"] == 101
    assert r.detail["total_lines"] == 500


@pytest.mark.asyncio
async def test_read_no_next_offset_at_end(tmp_ws):
    """next_offset is None when the window reaches the end of the file."""
    (tmp_ws / "f.txt").write_text("a\nb\nc\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_read(_read_req(tmp_ws, "f.txt", offset_lines=1, limit_lines=10))
    assert r.status == "SUCCESS"
    assert r.detail["next_offset"] is None


@pytest.mark.asyncio
async def test_read_paged(tmp_ws):
    """Reading with offset_lines returns the correct subsequent page."""
    (tmp_ws / "f.txt").write_text("\n".join(f"line{i}" for i in range(100)))
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r1 = await handle_workspace_read(_read_req(tmp_ws, "f.txt", offset_lines=1, limit_lines=50))
        r2 = await handle_workspace_read(_read_req(tmp_ws, "f.txt", offset_lines=51, limit_lines=50))
    assert r1.status == "SUCCESS"
    assert r2.status == "SUCCESS"
    assert "   51| line50" in r2.detail["content"]


# ---------------------------------------------------------------------------
# Search: total cap, glob filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_total_cap(tmp_ws):
    """Search respects the max_results cap and reports truncation."""
    (tmp_ws / "a.txt").write_text("match\n" * 200)
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_search(_search_req(tmp_ws, "match", max_results=5))
    assert r.status == "SUCCESS"
    assert len(r.detail["matches"]) <= 5
    assert "+" in r.message or "truncated" in r.message.lower()


@pytest.mark.asyncio
async def test_search_glob_filter(tmp_ws):
    """Search with glob only returns matching file paths."""
    (tmp_ws / "a.py").write_text("hello\n")
    (tmp_ws / "b.txt").write_text("hello\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_search(_search_req(tmp_ws, "hello", glob="*.py"))
    assert r.status == "SUCCESS"
    paths = [m["path"] for m in r.detail.get("matches", [])]
    for p in paths:
        assert p.endswith(".py")


@pytest.mark.asyncio
async def test_list_files(tmp_ws):
    """List returns files under the path, capped at max_entries."""
    for i in range(10):
        (tmp_ws / f"file{i}.txt").write_text(f"content{i}\n")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_list(_list_req(tmp_ws, glob="*.txt", max_entries=5))
    assert r.status == "SUCCESS"
    assert len(r.detail["entries"]) <= 5


@pytest.mark.asyncio
async def test_list_files_with_glob(tmp_ws):
    """List honours the glob filter."""
    (tmp_ws / "a.py").write_text("x")
    (tmp_ws / "b.txt").write_text("y")
    mock_resolve = _resolve(tmp_ws)
    with patch("services.execution.handlers.workspace._resolve_workspace_info", mock_resolve):
        r = await handle_workspace_list(_list_req(tmp_ws, glob="*.py"))
    assert r.status == "SUCCESS"
    for e in r.detail["entries"]:
        assert e.endswith(".py")


# ---------------------------------------------------------------------------
# Shell: stderr tail survives truncation, full output file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_stderr_survives(tmp_ws):
    """stderr is preserved in the result detail."""
    with patch("services.execution.handlers.workspace._resolve_workspace_info", _resolve(tmp_ws)), \
         patch("services.execution.handlers.workspace._sandbox_run", new_callable=AsyncMock,
               return_value=(1, "out", "err message")):
        r = await handle_workspace_shell(_shell_req("false"))
    assert "err message" in r.detail.get("stderr", "")


@pytest.mark.asyncio
async def test_shell_full_output_file(tmp_ws):
    """Full stdout+stderr is saved to .raven/last_output.txt."""
    with patch("services.execution.handlers.workspace._resolve_workspace_info", _resolve(tmp_ws)), \
         patch("services.execution.handlers.workspace._sandbox_run", new_callable=AsyncMock,
               return_value=(1, "stdout payload", "stderr payload")):
        r = await handle_workspace_shell(_shell_req("false"))
    output_file = os.path.join(tmp_ws, ".raven", "last_output.txt")
    assert os.path.isfile(output_file)
    with open(output_file, encoding="utf-8") as f:
        full = f.read()
    assert "stdout payload" in full
    assert "stderr payload" in full
    assert "full_output" in r.detail


@pytest.mark.asyncio
async def test_shell_truncation_reports_file(tmp_ws):
    """When output is truncated, the result mentions the full output file."""
    big = "x" * 20000
    with patch("services.execution.handlers.workspace._resolve_workspace_info", _resolve(tmp_ws)), \
         patch("services.execution.handlers.workspace._sandbox_run", new_callable=AsyncMock,
               return_value=(1, big, "")):
        r = await handle_workspace_shell(_shell_req("bigcmd"))
    assert "full_output" in r.detail
    assert "last_output.txt" in r.detail["full_output"]


@pytest.mark.asyncio
async def test_plain_reads_stay_plain_for_other_callers(tmp_ws):
    """Raven memory and other service reads must not get line numbers mixed into the text."""
    (tmp_ws / "raven_memory.md").write_text("lesson one\nlesson two\n")
    with patch("services.execution.handlers.workspace._resolve_workspace_info", _resolve(tmp_ws)):
        r = await handle_workspace_read(_read_req(tmp_ws, "raven_memory.md", line_numbers=False))
    assert r.detail["content"] == "lesson one\nlesson two\n"
