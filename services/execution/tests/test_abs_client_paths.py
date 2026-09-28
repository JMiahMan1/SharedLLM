# services/execution/tests/test_abs_client_paths.py
"""BUG-12: abs_client must speak the real ABS API.

Every route verified against the ABS server source of truth —
``advplyr/audiobookshelf`` ``server/routers/ApiRouter.js`` (master,
fetched 2026-09-27; the router is mounted under ``/api``):

* ``GET  /api/items/:id`` (:108)                      — `?expanded=1`
* ``GET  /api/me/progress/:id/:episodeId?`` (:183)
* ``PATCH /api/me/progress/:libraryItemId/:episodeId?`` (:185)  — was POST + wrong path
* ``GET  /api/me/items-in-progress`` (:191)
* ``POST /api/items/:id/play`` (:117)
* ``POST /api/session/:id/sync`` (:242)
* ``POST /api/session/:id/close`` (:243)
* ``GET  /api/libraries/:id/search`` (:84)
* ``GET  /api/libraries/:id/personalized`` (:82)
* ``POST /session/local`` (:238), ``PATCH /me/progress/batch/update`` (:184),
  ``GET /me/listening-sessions`` (:179), ``GET /libraries/:id/collections`` (:80),
  ``GET /libraries/:id/series`` (:78), ``GET /me/progress`` (:176)

Live probes of ``https://abs.sumemail.com`` could not discriminate routes:
ABS auth runs before routing and returns 401 for every path (including
nonexistent ones), so the source router above is the authority.
"""
import re

import aiohttp
import pytest
from aioresponses import aioresponses

from services.execution import abs_client

# aioresponses 0.7.9 predates aiohttp 3.14, where ClientResponse requires a
# `stream_writer` kwarg. Shim it (same as gateway tests/conftest_media.py).
if not getattr(aiohttp.ClientResponse.__init__, "_stream_writer_shim", False):

    class _StreamWriterStub:
        output_size = 0

    _orig_client_response_init = aiohttp.ClientResponse.__init__

    def _client_response_init(self, *args, **kwargs):
        kwargs.setdefault("stream_writer", _StreamWriterStub())
        return _orig_client_response_init(self, *args, **kwargs)

    _client_response_init._stream_writer_shim = True
    aiohttp.ClientResponse.__init__ = _client_response_init

ABS = "http://abs.local:13378"
KEY = "abs-key-1"


@pytest.fixture
def upstream():
    with aioresponses() as mock:
        pattern = re.compile(re.escape(ABS) + r".*")
        mock.get(pattern, payload={"ok": True})
        mock.post(pattern, payload={"ok": True})
        mock.patch(pattern, payload={"ok": True})
        yield mock


def _calls(upstream):
    return [(method, str(url)) for (method, url) in upstream.requests if ABS in str(url)]


async def _assert_call(upstream, method, path_and_query):
    calls = _calls(upstream)
    assert calls, "no ABS request was made"
    assert any(
        m == method and u.startswith(path_and_query) for m, u in calls
    ), f"expected {method} {path_and_query}, got {calls}"


@pytest.mark.parametrize(
    "coro, method, expected",
    [
        (lambda: abs_client.get_book(ABS, KEY, "book1"), "GET", "/api/items/book1?expanded=1"),
        (lambda: abs_client.get_progress(ABS, KEY), "GET", "/api/me/progress"),
        (lambda: abs_client.get_book_progress(ABS, KEY, "book1"), "GET", "/api/me/progress/book1"),
        (
            lambda: abs_client.update_progress(ABS, KEY, "book1", 12.5, 100.0),
            "PATCH",
            "/api/me/progress/book1",
        ),
        (lambda: abs_client.get_items_in_progress(ABS, KEY), "GET", "/api/me/items-in-progress"),
        (lambda: abs_client.play_item(ABS, KEY, "book1"), "POST", "/api/items/book1/play"),
        (
            lambda: abs_client.sync_session_position(ABS, KEY, "s1", 1.0, 2.0, 3.0),
            "POST",
            "/api/session/s1/sync",
        ),
        (lambda: abs_client.close_session(ABS, KEY, "s1"), "POST", "/api/session/s1/close"),
        (lambda: abs_client.sync_local_session(ABS, KEY, {"id": "s2"}), "POST", "/api/session/local"),
        (
            lambda: abs_client.get_listening_sessions(ABS, KEY, limit=5),
            "GET",
            "/api/me/listening-sessions?limit=5",
        ),
        (
            lambda: abs_client.batch_update_progress(
                ABS, KEY, [{"libraryItemId": "b", "currentTime": 1.0, "duration": 2.0}]
            ),
            "PATCH",
            "/api/me/progress/batch/update",
        ),
        (
            lambda: abs_client.search_library_items(ABS, KEY, "lib1", "hobbit", limit=10),
            "GET",
            # yarl normalizes query params alphabetically (limit before q)
            "/api/libraries/lib1/search?limit=10&q=hobbit",
        ),
        (
            lambda: abs_client.get_personalized_shelves(ABS, KEY, "lib1"),
            "GET",
            "/api/libraries/lib1/personalized",
        ),
        (lambda: abs_client.get_library_collections(ABS, KEY, "lib1"), "GET", "/api/libraries/lib1/collections"),
        (lambda: abs_client.get_library_series(ABS, KEY, "lib1"), "GET", "/api/libraries/lib1/series"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_abs_routes_match_real_api(upstream, coro, method, expected):
    result = await coro()
    assert "error" not in result, f"ABS call failed: {result}"
    await _assert_call(upstream, method, ABS + expected)
