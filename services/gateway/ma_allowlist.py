"""Music Assistant JSON-RPC command allowlist (MEDIA_OVERHAUL §7.3, BUG-04).

Only commands the UI is allowed to send through the browser WebSocket proxy
(WS /api/ma-jsonrpc) may reach Music Assistant. Anything else — in particular
admin/config commands — is answered locally with an error frame and never
forwarded upstream.

The list is the §7.3 allowlist from docs/MEDIA_OVERHAUL.md. Command names were
cross-checked against every command the legacy UI actually sends
(services/ui/src/lib/maWebPlayer.ts, services/ui/src/pages/Media.tsx);
verification against a live MA /api-docs is tracked in
docs/MEDIA_UPSTREAM_API_NOTES.md.
"""
import json

MA_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {
        "players/all",
        "players/get",
        "players/cmd/play",
        "players/cmd/pause",
        "players/cmd/play_pause",
        "players/cmd/stop",
        "players/cmd/seek",
        "players/cmd/volume_set",
        "players/cmd/volume_mute",
        "players/cmd/group",
        "players/cmd/group_many",
        "players/cmd/ungroup",
        "player_queues/all",
        "player_queues/get",
        "player_queues/items",
        "player_queues/play_media",
        "player_queues/next",
        "player_queues/previous",
        "player_queues/play_index",
        "player_queues/move_item",
        "player_queues/delete_item",
        "player_queues/clear",
        "player_queues/shuffle",
        "player_queues/repeat",
        "player_queues/transfer",
        "player_queues/seek",
        "player_queues/skip",
        "music/search",
        "music/item_by_uri",
        "music/recently_played_items",
        "music/in_progress_items",
        "music/recommendations",
        "music/favorites/add_item",
        "music/favorites/remove_item",
        "music/albums/library_items",
        "music/albums/album_tracks",
        "music/artists/library_items",
        "music/artists/artist_albums",
        "music/artists/artist_tracks",
        "music/playlists/library_items",
        "music/playlists/playlist_tracks",
        "music/playlists/add_playlist_tracks",
        "music/playlists/remove_playlist_tracks",
        "music/tracks/library_items",
        "music/radios/library_items",
        "music/podcasts/library_items",
        "music/podcasts/podcast_episodes",
    }
)


def forbidden_frame(message_id) -> str:
    """Build the error frame sent back to the browser for a rejected command."""
    return json.dumps({"error_code": "forbidden", "message_id": message_id})


def validate_ma_frame(raw: str) -> str | None:
    """Return an error frame JSON string if the browser frame must not be
    forwarded to Music Assistant, else None (frame may be forwarded).

    A frame is forwarded only when it is a JSON object whose ``command`` is in
    :data:`MA_COMMAND_ALLOWLIST`.
    """
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return forbidden_frame(None)
    if not isinstance(frame, dict):
        return forbidden_frame(None)
    command = frame.get("command")
    if not isinstance(command, str) or command not in MA_COMMAND_ALLOWLIST:
        return forbidden_frame(frame.get("message_id"))
    return None
