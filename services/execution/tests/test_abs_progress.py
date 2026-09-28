"""BUG-13 (P2-T4): ABS progress values passed through; fixed upstream call count.

Plan row: handlers/audiobookshelf.py:308,316,437 — progress hardcoded to "0%";
last_played fetched progress per book (up to 20 sequential calls). Fix: a single
GET /api/me/items-in-progress for metadata joined with a single GET /api/me/progress
for every book's progress values (no per-item calls, real percentages).
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.audiobookshelf import _handle_last_played, _handle_progress
from services.execution.schemas import AudiobookshelfRequest, UserContext

ABS_URL = "http://abs.local:13378"
ABS_KEY = "test_api_key"
PATCH = "services.execution.handlers.audiobookshelf.abs_client."

ITEMS = {
    "libraryItems": [
        {
            "id": "book-1",
            "media": {
                "metadata": {"title": "Atomic Habits", "authorName": "James Clear"},
                "duration": 32400,
                "chapters": [],
            },
            "progressLastUpdate": 1718119800000,
        },
        {
            "id": "book-2",
            "media": {
                "metadata": {"title": "Deep Work", "authorName": "Cal Newport"},
                "duration": 10000,
                "chapters": [],
            },
            "progressLastUpdate": 1718119700000,
        },
    ]
}

ALL_PROGRESS = {
    "mediaProgress": [
        {"libraryItemId": "book-1", "currentTime": 13608, "duration": 32400, "isFinished": False, "progress": 0.42},
        {"libraryItemId": "book-2", "currentTime": 8500, "duration": 10000, "isFinished": True, "progress": 0.85},
    ]
}


def _req() -> AudiobookshelfRequest:
    ctx = UserContext(ha_url="http://ha.local", ha_token="test-ha-token", user="testuser")
    return AudiobookshelfRequest(user_context=ctx, action="progress")


@pytest.mark.asyncio
async def test_progress_action_passes_percent_through():
    """action=progress reports real percentages, not the hardcoded "0%"."""
    get_items = AsyncMock(return_value=ITEMS)
    get_all = AsyncMock(return_value=ALL_PROGRESS)
    get_book = AsyncMock(return_value={})
    with (
        patch(PATCH + "get_items_in_progress", new=get_items),
        patch(PATCH + "get_progress", new=get_all),
        patch(PATCH + "get_book_progress", new=get_book),
    ):
        result = await _handle_progress(ABS_URL, ABS_KEY, _req())

    assert result.status == "SUCCESS"
    summaries = result.detail["in_progress"]
    assert [s["progress"] for s in summaries] == ["42%", "85%"]
    assert summaries[0]["title"] == "Atomic Habits"
    # Fixed call count: one metadata fetch + one fetch of ALL progress values.
    assert get_items.await_count == 1
    assert get_all.await_count == 1
    assert get_book.await_count == 0


@pytest.mark.asyncio
async def test_last_played_single_progress_call_not_per_book():
    """last_played joins one progress fetch instead of fetching per book."""
    get_items = AsyncMock(return_value=ITEMS)
    get_all = AsyncMock(return_value=ALL_PROGRESS)
    get_book = AsyncMock(return_value={})
    with (
        patch(PATCH + "get_items_in_progress", new=get_items),
        patch(PATCH + "get_progress", new=get_all),
        patch(PATCH + "get_book_progress", new=get_book),
    ):
        result = await _handle_last_played(ABS_URL, ABS_KEY)

    assert result.status == "SUCCESS"
    books = result.detail["books"]
    # Sorted by progressLastUpdate desc: book-1 first.
    assert books[0]["id"] == "book-1"
    assert books[0]["progress"] == 42
    assert books[0]["is_complete"] is False
    assert books[1]["progress"] == 85
    assert books[1]["is_complete"] is True
    assert get_items.await_count == 1
    assert get_all.await_count == 1
    assert get_book.await_count == 0


@pytest.mark.asyncio
async def test_last_played_progress_error_degrades_to_zero():
    """If the progress fetch fails, metadata is still returned with 0%."""
    get_items = AsyncMock(return_value=ITEMS)
    get_all = AsyncMock(return_value={"error": "API key invalid"})
    get_book = AsyncMock(return_value={})
    with (
        patch(PATCH + "get_items_in_progress", new=get_items),
        patch(PATCH + "get_progress", new=get_all),
        patch(PATCH + "get_book_progress", new=get_book),
    ):
        result = await _handle_last_played(ABS_URL, ABS_KEY)

    assert result.status == "SUCCESS"
    books = result.detail["books"]
    assert len(books) == 2
    assert all(b["progress"] == 0 for b in books)
    assert all(b["is_complete"] is False for b in books)
    assert get_all.await_count == 1
    assert get_book.await_count == 0
