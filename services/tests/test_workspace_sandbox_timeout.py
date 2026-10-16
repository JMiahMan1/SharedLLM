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


def test_deleting_a_workspace_stops_its_port_forwards():
    import threading

    stops = {k: threading.Event() for k in (("w1", 9001), ("w1", 9002), ("w2", 9003))}
    for key, evt in stops.items():
        ws._ACTIVE_PORT_FORWARDS[key] = (None, evt, 3000, "10.0.0.2")
    try:
        assert ws.unexpose_all_workspace_ports("w1") == 2
        assert stops[("w1", 9001)].is_set() and stops[("w1", 9002)].is_set()
        assert not stops[("w2", 9003)].is_set() and ("w2", 9003) in ws._ACTIVE_PORT_FORWARDS
    finally:
        ws._ACTIVE_PORT_FORWARDS.clear()
