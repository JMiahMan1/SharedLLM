"""The execution background loops keep running after a failed pass, and say so.

They used to wrap each pass in ``contextlib.suppress(Exception)``, so a pass
that failed every time (a removed endpoint, a bad credential) left no trace.
"""
import asyncio
import logging

import pytest

from services.execution import leave_by, watch_glance


class _Stop(Exception):
    pass


@pytest.mark.parametrize(
    "module, loop_name, pass_name",
    [(watch_glance, "glance_loop", "push_glances"), (leave_by, "leave_by_loop", "remind_once")],
)
def test_a_failing_pass_is_logged_and_the_loop_continues(module, loop_name, pass_name, monkeypatch, caplog):
    calls = {"pass": 0, "sleep": 0}

    async def failing_pass():
        calls["pass"] += 1
        raise RuntimeError("endpoint gone")

    async def fake_sleep(_seconds):
        calls["sleep"] += 1
        if calls["sleep"] > 2:
            raise _Stop

    monkeypatch.setattr(module, pass_name, failing_pass)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.WARNING), pytest.raises(_Stop):
        asyncio.run(getattr(module, loop_name)())

    assert calls["pass"] == 2  # survived the first failure
    assert sum("RuntimeError: endpoint gone" in r.getMessage() for r in caplog.records) == 2
