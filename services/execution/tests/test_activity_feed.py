"""The app's activity beacon handler: page views land on the activity feed."""
from unittest.mock import AsyncMock

from services.execution.handlers import activity_feed
from services.execution.schemas import ActivityRequest


def _req(**overrides):
    payload = {
        "user_context": {"user": "dad"},
        "feature": "notes",
        "item": "/notes",
    }
    payload.update(overrides)
    return ActivityRequest(**payload)


async def test_a_page_view_lands_on_the_feed(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(activity_feed.activity, "record", recorded)

    result = await activity_feed.handle_activity(_req())

    assert result.status == "SUCCESS"
    recorded.assert_awaited_once()
    args, kwargs = recorded.await_args
    assert args == ("dad", "notes")
    assert kwargs["action"] == "view"
    assert kwargs["item"] == "/notes"


async def test_a_blank_feature_still_records_something(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(activity_feed.activity, "record", recorded)

    await activity_feed.handle_activity(_req(feature="   "))

    args, _kwargs = recorded.await_args
    assert args == ("dad", "app")
