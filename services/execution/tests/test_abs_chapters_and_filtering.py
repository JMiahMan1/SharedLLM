"""Regression tests for Audiobookshelf chapter start/end parsing and unplayable book filtering."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.execution import abs_client
from services.execution.handlers import audiobookshelf as abs_handler
from services.execution.schemas import AudiobookshelfRequest, UserContext


def _req(**kwargs) -> AudiobookshelfRequest:
    return AudiobookshelfRequest(
        user_context=UserContext(user="zachary"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_abs_list_and_search_skip_ebook_only_and_missing_items(monkeypatch) -> None:
    playable = {
        "id": "book-audio",
        "isMissing": False,
        "media": {
            "duration": 3600,
            "numAudioFiles": 12,
            "metadata": {"title": "Wild at Heart", "authorName": "John Eldredge"},
        },
    }
    ebook_only = {
        "id": "book-epub",
        "isMissing": False,
        "media": {
            "duration": 0,
            "numAudioFiles": 0,
            "ebookFile": {"ino": "123"},
            "metadata": {"title": "Wild at Heart", "authorName": "John Eldredge"},
        },
    }
    missing = {
        "id": "book-missing",
        "isMissing": True,
        "media": {
            "duration": 3600,
            "numAudioFiles": 1,
            "metadata": {"title": "Wild at Heart", "authorName": "John Eldredge"},
        },
    }

    monkeypatch.setattr(
        abs_client,
        "get_library_items",
        AsyncMock(return_value={"results": [playable, ebook_only, missing]}),
    )
    list_res = await abs_handler._handle_list("http://abs", "k", _req(action="list", library_id="lib1", limit=50))
    assert list_res.status == "SUCCESS"
    assert [b["id"] for b in list_res.detail["books"]] == ["book-audio"]

    monkeypatch.setattr(abs_client, "get_libraries", AsyncMock(return_value={"libraries": [{"id": "lib1"}]}))
    monkeypatch.setattr(
        abs_client,
        "search_library_items",
        AsyncMock(return_value={"book": [{"libraryItem": playable}, {"libraryItem": ebook_only}, {"libraryItem": missing}]}),
    )
    search_res = await abs_handler._handle_search("http://abs", "k", _req(action="search", query="Wild", limit=20))
    assert search_res.status == "SUCCESS"
    assert [b["id"] for b in search_res.detail["books"]] == ["book-audio"]


@pytest.mark.asyncio
async def test_abs_last_played_reads_real_chapter_start_and_end(monkeypatch) -> None:
    monkeypatch.setattr(
        abs_client,
        "get_items_in_progress",
        AsyncMock(return_value={
            "libraryItems": [{
                "id": "book-audio",
                "progressLastUpdate": 1700000000000,
                "media": {
                    "duration": 5400,
                    "metadata": {"title": "Wild at Heart", "authorName": "John Eldredge"},
                    "chapters": [
                        {"id": 0, "start": 0, "end": 1800, "title": "Chapter 1"},
                        {"id": 1, "start": 1800, "end": 3900, "title": "Chapter 2"},
                    ],
                },
            }],
        }),
    )
    monkeypatch.setattr(abs_handler, "_all_progress_by_item", AsyncMock(return_value={}))

    res = await abs_handler._handle_last_played("http://abs", "k")
    assert res.status == "SUCCESS"
    chapters = res.detail["books"][0]["chapters"]
    assert chapters == [
        {"title": "Chapter 1", "start": 0, "end": 1800, "duration": 1800.0, "startTime": 0},
        {"title": "Chapter 2", "start": 1800, "end": 3900, "duration": 2100.0, "startTime": 1800},
    ]
