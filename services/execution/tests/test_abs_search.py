"""BUG-14 (P2-T5): ABS search is server-side and parallel — no library enumeration.

Plan row: handlers/audiobookshelf.py:80-107, abs_client.py:137 — search pulled
every item of every library (up to 51x500). Fix: per-library
GET /api/libraries/{id}/search?q=&limit= run in parallel with asyncio.gather.
Author-name queries keep returning books via GET /api/authors/:id?include=items
(the Media page author chip re-searches by name), never via /items listing.
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.audiobookshelf import _handle_search
from services.execution.schemas import AudiobookshelfRequest, UserContext

ABS_URL = "http://abs.local:13378"
ABS_KEY = "test_api_key"
PATCH = "services.execution.handlers.audiobookshelf.abs_client."

HOBBIT_ITEM = {
    "libraryItem": {
        "id": "abc123",
        "media": {
            "metadata": {
                "title": "The Hobbit",
                "authorName": "J.R.R. Tolkien",
                "narratorName": "Rob Inglis",
                "seriesName": "The Hobbit",
                "publishedYear": "1937",
                "genres": ["Fantasy"],
            },
            "duration": 54000,
        },
    }
}

PODCAST_ITEM = {
    "libraryItem": {
        "id": "pod-1",
        "media": {
            "metadata": {"title": "The Daily", "authorName": "NYT"},
            "duration": 0,
        },
    }
}

ROWLING_BOOKS = {
    "libraryItems": [
        {
            "id": "hp-1",
            "media": {"metadata": {"title": "Harry Potter", "authorName": "J.K. Rowling"}, "duration": 100},
        }
    ],
}


def _req(query: str, limit: int = 10) -> AudiobookshelfRequest:
    ctx = UserContext(ha_url="http://ha.local", ha_token="t", user="testuser")
    return AudiobookshelfRequest(user_context=ctx, action="search", query=query, limit=limit)


@pytest.mark.asyncio
async def test_search_server_side_parallel_no_listing_calls():
    """Search hits /search per library in parallel and never lists library items."""
    get_libraries = AsyncMock(return_value={
        "libraries": [
            {"id": "lib-books", "name": "Books", "mediaType": "book"},
            {"id": "lib-pods", "name": "Podcasts", "mediaType": "podcast"},
        ]
    })

    async def fake_search(_url, _key, lib_id, query, limit=10):
        if lib_id == "lib-books":
            return {"book": [HOBBIT_ITEM], "series": [], "authors": []}
        return {"podcast": [PODCAST_ITEM], "episodes": []}

    search_mock = AsyncMock(side_effect=fake_search)
    list_mock = AsyncMock(return_value=[])
    get_author = AsyncMock(return_value={"libraryItems": []})

    with (
        patch(PATCH + "get_libraries", new=get_libraries),
        patch(PATCH + "search_library_items", new=search_mock),
        patch(PATCH + "get_all_library_items", new=list_mock),
        patch(PATCH + "get_author", new=get_author),
    ):
        result = await _handle_search(ABS_URL, ABS_KEY, _req("hobbit"))

    assert result.status == "SUCCESS"
    assert [b["title"] for b in result.detail["books"]] == ["The Hobbit"]
    assert [p["title"] for p in result.detail["podcasts"]] == ["The Daily"]
    # Authors include distinct authors of returned items (legacy chip behavior).
    assert [a["name"] for a in result.detail["authors"]] == ["J.R.R. Tolkien"]
    assert result.detail["total"] == 3
    # One server-side search per library — no /items enumeration ever.
    assert search_mock.await_count == 2
    assert list_mock.await_count == 0


@pytest.mark.asyncio
async def test_search_author_name_returns_books_via_author_endpoint():
    """Author-chip re-search (query = author name) still yields their books."""
    get_libraries = AsyncMock(return_value={"libraries": [{"id": "lib-books", "name": "Books", "mediaType": "book"}]})
    search_mock = AsyncMock(return_value={"book": [], "series": [], "authors": [{"id": "au_1", "name": "J.K. Rowling", "numBooks": 1}]})
    list_mock = AsyncMock(return_value=[])
    get_author = AsyncMock(return_value=ROWLING_BOOKS)

    with (
        patch(PATCH + "get_libraries", new=get_libraries),
        patch(PATCH + "search_library_items", new=search_mock),
        patch(PATCH + "get_all_library_items", new=list_mock),
        patch(PATCH + "get_author", new=get_author),
    ):
        result = await _handle_search(ABS_URL, ABS_KEY, _req("J.K. Rowling"))

    assert result.status == "SUCCESS"
    books = result.detail["books"]
    assert [b["title"] for b in books] == ["Harry Potter"]
    assert books[0]["id"] == "hp-1"
    assert [a["name"] for a in result.detail["authors"]] == ["J.K. Rowling"]
    # Author's books came from GET /api/authors/:id, never from an items listing.
    get_author.assert_awaited_once_with(ABS_URL, ABS_KEY, "au_1", include="items")
    assert list_mock.await_count == 0


@pytest.mark.asyncio
async def test_search_series_books_merged_and_deduped():
    """Series matches contribute books; duplicates across sources collapse."""
    get_libraries = AsyncMock(return_value={"libraries": [{"id": "lib-books", "name": "Books", "mediaType": "book"}]})
    search_mock = AsyncMock(return_value={
        "book": [HOBBIT_ITEM],
        "series": [{"series": {"name": "The Hobbit"}, "books": [HOBBIT_ITEM["libraryItem"], {
            "id": "abc124",
            "media": {"metadata": {"title": "There and Back Again", "authorName": "J.R.R. Tolkien"}, "duration": 1},
        }]}],
        "authors": [],
    })
    list_mock = AsyncMock(return_value=[])
    get_author = AsyncMock(return_value={"libraryItems": []})

    with (
        patch(PATCH + "get_libraries", new=get_libraries),
        patch(PATCH + "search_library_items", new=search_mock),
        patch(PATCH + "get_all_library_items", new=list_mock),
        patch(PATCH + "get_author", new=get_author),
    ):
        result = await _handle_search(ABS_URL, ABS_KEY, _req("hobbit"))

    assert result.status == "SUCCESS"
    ids = [b["id"] for b in result.detail["books"]]
    assert ids == ["abc123", "abc124"]
    assert list_mock.await_count == 0
