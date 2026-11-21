"""An absolute path is confined to the caller's own workspace, not the whole root.

``resolve_safe_path`` used to accept any absolute path under the workspace
*root*, so a user could name ``<root>/users/<someone else>/...`` and read or
overwrite another user's files through their own workspace.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from services.workspace_runtime import main as runtime


@pytest.fixture
def two_workspaces(tmp_path: Path, monkeypatch):
    root = tmp_path.resolve()
    mine = root / "users" / "me" / "proj"
    theirs = root / "users" / "them" / "proj"
    for folder in (mine, theirs):
        folder.mkdir(parents=True)
        (folder / "model.stl").write_text("solid x\n")
    monkeypatch.setattr(runtime, "get_workspace_root", lambda: root)
    return root, mine, theirs


def test_an_absolute_path_inside_my_workspace_resolves(two_workspaces):
    _root, mine, _theirs = two_workspaces

    assert runtime.resolve_safe_path(mine, str(mine / "model.stl")) == mine / "model.stl"


def test_an_absolute_path_into_another_workspace_is_forbidden(two_workspaces):
    _root, mine, theirs = two_workspaces

    with pytest.raises(HTTPException) as excinfo:
        runtime.resolve_safe_path(mine, str(theirs / "model.stl"))
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        runtime.resolve_safe_path(mine, str(theirs / "new.stl"), must_exist=False)
    assert excinfo.value.status_code == 403


def test_an_absolute_path_that_climbs_out_is_forbidden(two_workspaces):
    _root, mine, _theirs = two_workspaces

    with pytest.raises(HTTPException) as excinfo:
        runtime.resolve_safe_path(mine, str(mine / ".." / ".." / "them" / "proj" / "model.stl"))
    assert excinfo.value.status_code == 403


def test_a_root_level_caller_still_resolves_a_workspace_local_path(two_workspaces):
    """The few callers that resolve a workspace's own local_path pass the root as base."""
    root, mine, _theirs = two_workspaces

    assert runtime.resolve_safe_path(root, str(mine), must_exist=False) == mine
