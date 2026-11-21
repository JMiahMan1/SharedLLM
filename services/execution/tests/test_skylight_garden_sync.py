"""Every chore tick-off reaches Garden, whichever way it was made.

``complete_skylight_chore`` is the one path all completions take (the app via
the gateway, Jarvis's chores handler, Garden's own mirror), so it is where a
linked plant learns its chore was done -- and un-done. Garden's own mirror is
not reported back (that would log the same watering twice), and a Garden that
does not answer is logged, never a failed tick-off.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import aiohttp
import pytest

from services.execution import main


class _Resp:
    def __init__(self, status):
        self.status = status

    async def text(self):
        return "boom" if self.status >= 400 else ""


@pytest.fixture()
def garden(monkeypatch):
    sent: list[tuple[str, dict]] = []
    state = {"status": 200, "raises": None}

    class _Session:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        @asynccontextmanager
        async def post(self, url, json=None, headers=None):
            if state["raises"]:
                raise state["raises"]
            sent.append((url, json))
            yield _Resp(state["status"])

    async def session(_user=None):
        return object()

    async def skylight(_session, _method, _path, _body=None):
        return {}

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(main, "_get_skylight_session", session)
    monkeypatch.setattr(main, "_skylight_request", skylight)
    return sent, state


async def test_a_completion_tells_garden(garden):
    sent, _ = garden

    done = await main.complete_skylight_chore("52409380-2026-11-20", user="child_one", x_internal_secret=None)

    assert done["status"] == "SUCCESS"
    assert [url.rsplit("/", 1)[-1] for url, _ in sent] == ["skylight-chore-completed"]
    assert sent[0][1] == {"chore_id": "52409380-2026-11-20", "performed_by": "child_one"}


async def test_gardens_own_mirror_is_not_reported_back(garden):
    sent, _ = garden

    await main.complete_skylight_chore("52409380", user="child_one", x_internal_secret=None, origin="garden")

    assert sent == []


async def test_an_uncompletion_tells_garden_to_undo(garden):
    sent, _ = garden

    undone = await main.uncomplete_skylight_chore("52409380-2026-11-20", user="child_one", x_internal_secret=None)

    assert undone["status"] == "SUCCESS"
    assert [url.rsplit("/", 1)[-1] for url, _ in sent] == ["skylight-chore-uncompleted"]


@pytest.mark.parametrize(("status", "raises"), ((500, None), (200, aiohttp.ClientConnectionError("refused"))))
async def test_a_garden_problem_is_logged_not_a_failed_tick(garden, caplog, status, raises):
    _, state = garden
    state.update(status=status, raises=raises)

    with caplog.at_level(logging.WARNING, logger="execution"):
        done = await main.complete_skylight_chore("52409380", user="child_one", x_internal_secret=None)

    assert done["status"] == "SUCCESS"
    assert any("skylight-chore-completed" in record.getMessage() for record in caplog.records)
