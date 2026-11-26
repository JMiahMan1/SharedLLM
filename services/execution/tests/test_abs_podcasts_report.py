"""The podcast report: newest episode, last check, feed, and why a show is quiet.

A show can look abandoned for three unrelated reasons, and the report keeps them
apart because each has a different remedy: no feed address, a feed nobody polls,
or new episodes that nothing downloads. Nothing here knows a show by name.
"""
import time
from unittest.mock import AsyncMock

import pytest

from services.execution import abs_client, abs_podcasts

NOW = 1_790_000_000.0  # 2026-09-21T13:33:20Z
DAY = 86_400.0


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


def _episode(title: str, published: float) -> dict:
    return {"id": title.lower().replace(" ", "-"), "title": title, "publishedAt": _ms(published)}


def _show(
    *,
    title="A Show",
    episodes=None,
    checked_at=None,
    feed="https://feeds.example/show.rss",
    auto_download=False,
) -> dict:
    metadata = {"title": title, "author": "Someone", "feedUrl": feed}
    media = {"metadata": metadata, "coverPath": f"/metadata/items/{title}/cover.jpg", "episodes": episodes or []}
    if checked_at is not None:
        media["lastEpisodeCheck"] = _ms(checked_at)
    media["autoDownloadEpisodes"] = auto_download
    return {"id": f"item-{title}", "media": media}


def test_a_show_that_is_keeping_up_is_not_flagged():
    row = abs_podcasts.describe_show(
        _show(episodes=[_episode("Newest", NOW - 2 * DAY)], checked_at=NOW - DAY),
        library_id="lib", library_name="Podcasts", now=NOW,
    )

    assert row["title"] == "A Show"
    assert row["episodes"] == 1
    assert row["newest_episode_title"] == "Newest"
    assert row["days_since_episode"] == 2
    assert row["days_since_check"] == 1
    assert row["behind"] is False
    assert row["stale_check"] is False
    assert row["needs_feed"] is False


def test_a_show_with_no_feed_address_is_its_own_problem():
    row = abs_podcasts.describe_show(
        _show(feed="", episodes=[_episode("Old", NOW - 200 * DAY)], checked_at=NOW - 200 * DAY),
        library_id="lib", library_name="Podcasts", now=NOW,
    )

    assert row["needs_feed"] is True
    assert row["behind"] is True
    # A missing feed is a different fault from a quiet feed, and both are reported.
    assert row["stale_check"] is True


def test_a_feed_nobody_polls_is_reported_even_when_the_episodes_are_recent():
    """The live fault on 2026-11-25: every show was last checked months ago."""
    row = abs_podcasts.describe_show(
        _show(episodes=[_episode("Recent", NOW - 3 * DAY)], checked_at=NOW - 120 * DAY),
        library_id="lib", library_name="Podcasts", now=NOW,
    )

    assert row["behind"] is False
    assert row["stale_check"] is True
    assert row["days_since_check"] == 120


def test_a_show_that_was_never_checked_says_so():
    row = abs_podcasts.describe_show(
        _show(episodes=[_episode("Only", NOW - 400 * DAY)]),
        library_id="lib", library_name="Podcasts", now=NOW,
    )

    assert row["never_checked"] is True
    assert row["stale_check"] is True
    assert row["last_checked_at"] is None


def test_a_timestamp_in_seconds_is_read_as_seconds():
    """ABS has answered in both units; reading seconds as milliseconds dates a show to 1970."""
    show = _show(episodes=[_episode("E", NOW - DAY)])
    show["media"]["lastEpisodeCheck"] = int(NOW - DAY)

    row = abs_podcasts.describe_show(show, library_id="lib", library_name="Podcasts", now=NOW)

    assert row["days_since_check"] == 1


def test_only_the_libraries_the_server_calls_podcasts_are_read():
    libraries = [
        {"id": "b1", "name": "Books", "mediaType": "book"},
        {"id": "p1", "name": "Podcasts", "mediaType": "podcast"},
        {"id": "p2", "name": "Shows", "mediaType": "PODCASTS"},
    ]

    assert abs_podcasts.podcast_libraries(libraries) == [
        {"id": "p1", "name": "Podcasts"},
        {"id": "p2", "name": "Shows"},
    ]


@pytest.fixture
def upstream(monkeypatch):
    """A fake ABS: libraries, one library listing, and item details by id."""
    state = {"libraries": [], "items": {}, "details": {}, "detail_calls": []}

    async def get_libraries(abs_url, key):
        return {"libraries": state["libraries"]}

    async def get_all_library_items(abs_url, key, library_id, page_size=500):
        return state["items"].get(library_id, [])

    async def abs_get(abs_url, key, path, params=None):
        state["detail_calls"].append(path)
        item_id = path.rsplit("/", 1)[-1]
        return state["details"].get(item_id, {"error": "not found"})

    monkeypatch.setattr(abs_client, "get_libraries", get_libraries)
    monkeypatch.setattr(abs_client, "get_all_library_items", get_all_library_items)
    monkeypatch.setattr(abs_client, "abs_get", abs_get)
    return state


@pytest.mark.asyncio
async def test_the_report_reads_each_show_once_and_worst_first(upstream):
    upstream["libraries"] = [
        {"id": "books", "name": "Books", "mediaType": "book"},
        {"id": "pods", "name": "Podcasts", "mediaType": "podcast"},
    ]
    upstream["items"] = {"pods": [{"id": "kept"}, {"id": "quiet"}]}
    upstream["details"] = {
        "kept": _show(title="Kept", episodes=[_episode("Fresh", NOW - 2 * DAY)], checked_at=NOW - DAY),
        "quiet": _show(title="Quiet", feed="", episodes=[_episode("Old", NOW - 90 * DAY)]),
    }

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    assert [show["title"] for show in report["shows"]] == ["Quiet", "Kept"]
    assert sorted(upstream["detail_calls"]) == ["/api/items/kept", "/api/items/quiet"]
    assert report["libraries"] == [{"id": "pods", "name": "Podcasts"}]
    assert report["behind_days"] == abs_podcasts.BEHIND_DAYS


@pytest.mark.asyncio
async def test_episodes_already_in_the_listing_are_not_fetched_again(upstream):
    upstream["libraries"] = [{"id": "pods", "name": "Podcasts", "mediaType": "podcast"}]
    upstream["items"] = {"pods": [_show(title="Inline", episodes=[_episode("Fresh", NOW - DAY)], checked_at=NOW)]}

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    assert report["shows"][0]["episodes"] == 1
    assert upstream["detail_calls"] == []


@pytest.mark.asyncio
async def test_one_unreadable_show_does_not_empty_the_report(upstream):
    upstream["libraries"] = [{"id": "pods", "name": "Podcasts", "mediaType": "podcast"}]
    upstream["items"] = {"pods": [{"id": "gone"}, {"id": "fine"}]}
    upstream["details"] = {"fine": _show(title="Fine", episodes=[_episode("Fresh", NOW - DAY)], checked_at=NOW)}

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    titles = [show["title"] for show in report["shows"]]
    assert "Fine" in titles
    assert len(report["shows"]) == 2


@pytest.mark.asyncio
async def test_an_unknown_show_still_appears_marked(upstream):
    upstream["libraries"] = [{"id": "pods", "name": "Podcasts", "mediaType": "podcast"}]
    upstream["items"] = {"pods": [{"id": "gone", "media": {"metadata": {"title": "Gone"}}}]}

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    assert report["shows"][0]["title"] == "Gone"
    assert report["shows"][0]["error"]


@pytest.mark.asyncio
async def test_no_podcast_library_is_an_empty_report_not_an_error(upstream):
    upstream["libraries"] = [{"id": "books", "name": "Books", "mediaType": "book"}]

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    assert report["shows"] == []
    assert report["libraries"] == []
    assert "error" not in report


@pytest.mark.asyncio
async def test_an_unreachable_server_is_an_error_the_caller_can_show(upstream, monkeypatch):
    monkeypatch.setattr(abs_client, "get_libraries", AsyncMock(return_value={"error": "Audiobookshelf is unreachable"}))

    report = await abs_podcasts.podcast_report("http://abs", "k", now=NOW)

    assert report == {"error": "Audiobookshelf is unreachable"}


@pytest.mark.asyncio
async def test_auto_download_is_patched_on_the_item(monkeypatch):
    seen = {}

    async def abs_patch(abs_url, key, path, json=None):
        seen["path"] = path
        seen["json"] = json
        return {"id": "abc"}

    monkeypatch.setattr(abs_client, "abs_patch", abs_patch)

    reply = await abs_podcasts.set_auto_download("http://abs", "k", "abc", True)

    assert seen == {"path": "/api/items/abc/media", "json": {"autoDownloadEpisodes": True}}
    assert reply == {"item_id": "abc", "auto_download": True}


@pytest.mark.asyncio
async def test_auto_download_needs_a_show(monkeypatch):
    monkeypatch.setattr(abs_client, "abs_patch", AsyncMock(side_effect=AssertionError("must not call ABS")))

    assert "error" in await abs_podcasts.set_auto_download("http://abs", "k", "", True)


def test_the_report_is_about_a_month_of_silence_and_a_week_of_no_checks():
    """The two thresholds are the whole judgement, so they are pinned."""
    assert abs_podcasts.BEHIND_DAYS == 21
    assert abs_podcasts.STALE_CHECK_DAYS == 7
    assert abs_podcasts._iso(NOW) == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW))
