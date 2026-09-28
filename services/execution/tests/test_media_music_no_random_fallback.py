"""BUG-25 (P2-T16): failed music search must not play a random track.

`play_music` (services/execution/handlers/media.py) had two fallbacks that
silently played *something else* when the MASS search found nothing:

1. `get_library` with `order_by=random` — a random track from the library.
2. a last-resort `play_media` with the raw query as `media_id` (which MA
   rejects for plain text).

Both are gone: a failed/empty search returns FAILURE "No match for …" and
nothing is played.

Second half of BUG-25: `enqueue="replace"` was rewritten to `"play"` before
every `music_assistant.play_media` call. The HA service's `enqueue` field
accepts `play|replace|next|replace_next|add` (verified 2026-09-27 against
HA core `music_assistant/services.yaml`, cached in
`.tmp/ha_music_assistant_services.yaml`), and the schema's
`add|next|replace` values all map 1:1 — so the request's `enqueue` must pass
through unchanged (replace keeps its clear-queue semantics instead of
degrading to plain play).
"""
import asyncio
from unittest.mock import AsyncMock

from services.execution.handlers import media as media_handler
from services.execution.schemas import MediaPlayRequest, UserContext

P_ROKU = "services.execution.handlers.roku."
P_SAMSUNG = "services.execution.handlers.samsung."

HA_URL = "http://ha.local:8123"
HA_TOKEN = "tok"


def _ctx():
    return UserContext(user="bug25", ha_url=HA_URL, ha_token=HA_TOKEN)


def _req(query: str, enqueue: str = "replace") -> MediaPlayRequest:
    return MediaPlayRequest(
        user_context=UserContext(user="bug25", ha_url=HA_URL, ha_token=HA_TOKEN),
        entity_id="media_player.office",
        query=query,
        enqueue=enqueue,
    )


class _CallRecorder:
    """Stands in for ha_client.call_service; records every call."""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: dict[str, dict] = {}

    def set(self, service: str, response: dict):
        self.responses[service] = response

    async def __call__(self, ha_url, ha_token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append({"domain": domain, "service": service, "entity_id": entity_id, "service_data": service_data})
        return self.responses.get(service, {"ok": False, "error": f"unexpected {service}"})

    def by_service(self, service: str) -> list[dict]:
        return [c for c in self.calls if c["service"] == service]


def _empty_search():
    return {"ok": True, "service_response": {"service_response": {"tracks": [], "albums": [], "artists": [], "playlists": [], "radio": []}}}


def _track_search(uri="library://track/42"):
    return {"ok": True, "service_response": {"service_response": {"tracks": [{"uri": uri, "name": "Hit"}]}}}


def _patch_common(monkeypatch, recorder: _CallRecorder):
    """Pave every device-detection / resolution seam so play_music reaches the
    plain MASS search path with a recording call_service."""
    monkeypatch.setattr(media_handler, "MASS_CONFIG_ENTRY_ID", "ce-test")
    monkeypatch.setattr(media_handler, "resolve_mass_entity", AsyncMock(return_value="media_player.mass_office"))
    monkeypatch.setattr(f"{P_ROKU}is_roku_device", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{P_SAMSUNG}is_samsung_tv", AsyncMock(return_value=False))
    monkeypatch.setattr(media_handler.ha_client, "call_service", recorder)


def test_failed_search_returns_no_match_without_random_fallback(monkeypatch):
    """Empty MASS search → FAILURE 'No match', no get_library random, no play_media."""
    recorder = _CallRecorder()
    recorder.set("search", _empty_search())
    _patch_common(monkeypatch, recorder)

    result = asyncio.run(media_handler.play_music(_req("bohemian rhapsody"), "media_player.office", _ctx()))

    assert result.status == "FAILURE"
    assert "No match" in result.message
    assert "bohemian rhapsody" in result.message
    assert recorder.by_service("get_library") == []
    assert recorder.by_service("play_media") == []


def test_search_call_error_returns_no_match(monkeypatch):
    """Search service failure (ok=False) → FAILURE, same no-fallback contract."""
    recorder = _CallRecorder()
    recorder.set("search", {"ok": False, "error": "HA returned 500"})
    _patch_common(monkeypatch, recorder)

    result = asyncio.run(media_handler.play_music(_req("sde maha"), "media_player.office", _ctx()))

    assert result.status == "FAILURE"
    assert "No match" in result.message
    assert recorder.by_service("get_library") == []
    assert recorder.by_service("play_media") == []


def test_enqueue_replace_passes_through_to_play_media(monkeypatch):
    """enqueue='replace' must reach music_assistant.play_media unchanged."""
    recorder = _CallRecorder()
    recorder.set("search", _track_search())
    recorder.set("play_media", {"ok": True, "status_code": 200})
    _patch_common(monkeypatch, recorder)

    result = asyncio.run(media_handler.play_music(_req("nevermind", enqueue="replace"), "media_player.office", _ctx()))

    assert result.status == "SUCCESS"
    plays = recorder.by_service("play_media")
    assert len(plays) == 1
    assert plays[0]["service_data"]["enqueue"] == "replace"
    assert plays[0]["service_data"]["media_id"] == "library://track/42"


def test_enqueue_next_passes_through_to_play_media(monkeypatch):
    recorder = _CallRecorder()
    recorder.set("search", _track_search())
    recorder.set("play_media", {"ok": True, "status_code": 200})
    _patch_common(monkeypatch, recorder)

    result = asyncio.run(media_handler.play_music(_req("nevermind", enqueue="next"), "media_player.office", _ctx()))

    assert result.status == "SUCCESS"
    plays = recorder.by_service("play_media")
    assert len(plays) == 1
    assert plays[0]["service_data"]["enqueue"] == "next"


def test_direct_uri_enqueue_passes_through(monkeypatch):
    """A direct :// URI also passes its enqueue value through."""
    recorder = _CallRecorder()
    recorder.set("play_media", {"ok": True, "status_code": 200})
    _patch_common(monkeypatch, recorder)

    result = asyncio.run(media_handler.play_music(_req("library://track/7", enqueue="add"), "media_player.office", _ctx()))

    assert result.status == "SUCCESS"
    plays = recorder.by_service("play_media")
    assert len(plays) == 1
    assert plays[0]["service_data"]["enqueue"] == "add"
