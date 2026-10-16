"""Sandbox commands carry their time limit into the container, so one that
never exits ends by itself instead of holding a thread for good."""
from services import workspace_sandbox as ws


async def test_the_command_runs_under_timeout_in_the_container(monkeypatch):
    seen = {}

    def exec_blocking(workspace_id, host_path, cmd, **kwargs):
        seen["cmd"] = cmd
        return 124, "", ""

    monkeypatch.setattr(ws, "_under_mount_root", lambda p: True)
    monkeypatch.setattr(ws, "_exec_blocking", exec_blocking)
    res = await ws.run_workspace_cmd("w1", "/workspaces/w1", ["npm", "run", "dev"], timeout=30.5)
    assert seen["cmd"] == ["timeout", "-k", "5", "31", "npm", "run", "dev"]
    assert res["args"] == ["npm", "run", "dev"]
    assert res["returncode"] == 124 and "timed out after 30.5s" in res["stderr"]
