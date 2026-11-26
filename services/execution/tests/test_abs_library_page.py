"""The ABS library listing has to page: a 199-book library is not 49 books.

The Media page sends a flat ``offset``; the gateway turns it into an ABS page
number; the execution GET route used to drop it, so every page asked for page 0
and everything past the first page could not be reached or opened.
"""

from __future__ import annotations

import pytest

from services.execution import main as execution_main
from services.execution.schemas import ExecutionResult


@pytest.mark.asyncio
async def test_the_get_route_passes_the_page_on(monkeypatch):
    seen = {}

    async def fake_resolve(rag_user=""):
        return {"audiobookshelf_url": "http://abs.local:13378", "audiobookshelf_api_key": "k"}

    async def fake_handle(req):
        seen["page"] = req.page
        seen["limit"] = req.limit
        seen["action"] = req.action
        return ExecutionResult(status="SUCCESS", message="ok", service="audiobookshelf", detail={"books": []})

    monkeypatch.setattr(execution_main, "resolve_internal_user", fake_resolve)
    monkeypatch.setitem(__import__("sys").modules, "services.execution.handlers.audiobookshelf", __import__("services.execution.handlers.audiobookshelf", fromlist=["handle_audiobookshelf"]))
    monkeypatch.setattr(
        __import__("services.execution.handlers.audiobookshelf", fromlist=["handle_audiobookshelf"]),
        "handle_audiobookshelf",
        fake_handle,
    )

    await execution_main.handle_audiobookshelf_get(
        action="list", user_id="default", library_id="lib-1", limit=50, page=3
    )

    assert seen == {"page": 3, "limit": 50, "action": "list"}, seen
