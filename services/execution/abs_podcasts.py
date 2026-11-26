# services/execution/abs_podcasts.py
"""What every podcast in Audiobookshelf is doing, and what to do about it.

The complaint this answers is "the podcasts are months old", and the answer is
never one number: a show can be quiet because the maker is on a break, because
Audiobookshelf was never given its feed address, because nothing has polled the
feed, or because new episodes arrive but nothing downloads them. Each of those
has its own remedy, so the report keeps them apart instead of one "stale" flag.

Nothing here knows a show by name. The podcast libraries come from the server's
own library list and every fact below comes from the show's own record.
"""
import asyncio
import time
from typing import Any

from services.execution import abs_client

#: A show with nothing new for this long is worth a look. Chosen so a weekly
#: show that skips a week or two is not nagged about, and a monthly show that
#: is genuinely quiet is still reported (it is only ever "worth a look").
BEHIND_DAYS = 21

#: Audiobookshelf records when it last polled a feed. Older than this and the
#: feed is not being checked at all, whatever the newest episode says.
STALE_CHECK_DAYS = 7

#: How many shows are fetched at once. The whole report is one round trip per
#: show (ABS keeps episodes on the item, not in the library list).
FANOUT = 6


def _ms(value: Any) -> float | None:
    """An ABS millisecond timestamp as epoch seconds, or None when absent."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # ABS sometimes stores seconds; anything this small cannot be milliseconds.
    return number / 1000.0 if number > 100_000_000_000 else number


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _days(since: float | None, now: float) -> int | None:
    if since is None:
        return None
    return max(0, int((now - since) // 86400))


def _episodes(media: dict) -> list[dict]:
    episodes = media.get("episodes")
    return episodes if isinstance(episodes, list) else []


def _newest(episodes: list[dict]) -> tuple[float | None, str | None]:
    newest_at: float | None = None
    newest_title: str | None = None
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        at = _ms(episode.get("publishedAt")) or _ms(episode.get("addedAt"))
        if at is not None and (newest_at is None or at > newest_at):
            newest_at = at
            newest_title = episode.get("title") or None
    return newest_at, newest_title


def describe_show(item: dict, *, library_id: str, library_name: str, now: float) -> dict:
    """One show's row: episodes, the newest one, when ABS last checked, and why it might be quiet."""
    media = item.get("media") or {}
    meta = media.get("metadata") or {}
    episodes = _episodes(media)
    newest_at, newest_title = _newest(episodes)
    checked_at = _ms(media.get("lastEpisodeCheck"))
    feed = str(meta.get("feedUrl") or "").strip()
    auto_download = bool(media.get("autoDownloadEpisodes"))
    days_since_episode = _days(newest_at, now)
    days_since_check = _days(checked_at, now)

    return {
        "library_id": library_id,
        "library_name": library_name,
        "id": item.get("id"),
        "title": meta.get("title") or "Untitled",
        "author": meta.get("author") or meta.get("authorName") or "",
        "cover_path": media.get("coverPath") or "",
        "episodes": len(episodes),
        "newest_episode_at": _iso(newest_at),
        "newest_episode_title": newest_title,
        "last_checked_at": _iso(checked_at),
        "days_since_episode": days_since_episode,
        "days_since_check": days_since_check,
        "auto_download": auto_download,
        "has_feed": bool(feed),
        # The three separate reasons a show can look abandoned.
        "needs_feed": not feed,
        "never_checked": checked_at is None,
        "stale_check": days_since_check is None or days_since_check >= STALE_CHECK_DAYS,
        "behind": (days_since_episode is None or days_since_episode >= BEHIND_DAYS),
    }


def podcast_libraries(libraries: list[dict]) -> list[dict]:
    """The libraries ABS itself calls podcasts."""
    found = []
    for lib in libraries:
        if not isinstance(lib, dict):
            continue
        media_type = str(lib.get("mediaType") or "").lower()
        if "podcast" in media_type and lib.get("id"):
            found.append({"id": lib["id"], "name": lib.get("name") or "Podcasts"})
    return found


async def _one_show(abs_url: str, abs_api_key: str, item: dict, library: dict, now: float,
                    semaphore: asyncio.Semaphore) -> dict:
    """A show's row, with its episodes read from the item itself.

    The library listing can carry episodes already; when it does not, the item
    is fetched once. A failed fetch still yields a row, marked with an error, so
    one broken show cannot empty the report.
    """
    async with semaphore:
        raw = item
        if not _episodes(item.get("media") or {}) and item.get("id"):
            detail = await abs_client.abs_get(abs_url, abs_api_key, f"/api/items/{item['id']}")
            if "error" in detail:
                row = describe_show(item, library_id=library["id"], library_name=library["name"], now=now)
                row["error"] = detail["error"]
                return row
            raw = {**detail, "id": item.get("id") or detail.get("id")}
        return describe_show(raw, library_id=library["id"], library_name=library["name"], now=now)


async def podcast_report(abs_url: str, abs_api_key: str, *, now: float | None = None) -> dict:
    """Every podcast show in every podcast library, with the reason each looks quiet.

    Returns ``{"libraries": [...], "shows": [...], "behind_days": n,
    "stale_check_days": n}``, or ``{"error": "..."}`` when ABS cannot be read.
    """
    moment = time.time() if now is None else now
    libraries = await abs_client.get_libraries(abs_url, abs_api_key)
    if "error" in libraries:
        return libraries

    pods = podcast_libraries(libraries.get("libraries") or [])
    if not pods:
        return {"libraries": [], "shows": [], "behind_days": BEHIND_DAYS, "stale_check_days": STALE_CHECK_DAYS}

    semaphore = asyncio.Semaphore(FANOUT)
    jobs = []
    for library in pods:
        items = await abs_client.get_all_library_items(abs_url, abs_api_key, library["id"])
        if isinstance(items, dict) and "error" in items:
            return items
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                jobs.append(_one_show(abs_url, abs_api_key, item, library, moment, semaphore))

    shows = list(await asyncio.gather(*jobs))
    shows.sort(key=lambda row: (
        not row.get("behind"),
        not row.get("needs_feed"),
        (row.get("title") or "").lower(),
    ))
    return {
        "libraries": pods,
        "shows": shows,
        "behind_days": BEHIND_DAYS,
        "stale_check_days": STALE_CHECK_DAYS,
    }


async def set_auto_download(abs_url: str, abs_api_key: str, item_id: str, enabled: bool) -> dict:
    """Turn automatic downloading of a show's new episodes on or off.

    ABS takes this as an item media patch. It is the one podcast setting a
    listener can reasonably change without touching the server's schedule.
    """
    if not item_id:
        return {"error": "item_id is required"}
    reply = await abs_client.abs_patch(
        abs_url, abs_api_key, f"/api/items/{item_id}/media", json={"autoDownloadEpisodes": bool(enabled)}
    )
    if isinstance(reply, dict) and "error" in reply:
        return reply
    return {"item_id": item_id, "auto_download": bool(enabled)}
