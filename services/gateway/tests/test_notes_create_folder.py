"""A note created without a folder lands where Notes lists it."""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _request(body):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "folder"), [
    ({"title": "Groceries", "content": "milk"}, "Notes"),  # the dashboard's Quick Notes
    ({"title": "John 3:16", "category": "Bible"}, "Bible"),
])
async def test_the_folder_defaults_to_notes(monkeypatch, body, folder):
    from services.gateway import main

    sent = {}

    async def fake_proxy(request, path, payload, as_user=None):
        sent.update(payload)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)
    await main.proxy_create_note(_request(body))
    assert sent["action"] == "create" and sent["category"] == folder
