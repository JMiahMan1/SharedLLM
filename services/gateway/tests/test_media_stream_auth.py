"""§7.4 / BUG-07: /api/media/stream/* accepts signed media tokens (``?mt=``).

Devices (HA speakers, Cast, Roku) fetch the gateway stream URL with no
headers, so the short-lived signed media token in the query string is the
only auth they can present. The token is bound to a user: ``?user=`` must
match the user the token was signed for, and expired/tampered tokens are
rejected with 403 without falling back to any other credential.
"""
import time

from services.gateway.tests.conftest_media import mock_upstream
from services.shared.media_token import sign


def test_stream_abs_accepts_valid_media_token(client, upstream):
    mock_upstream(
        upstream, "GET", "http://abs.local:13378/api/items/book1/stream", payload="audio-bytes"
    )
    mt, _exp = sign("testuser")
    resp = client.get(
        "/api/media/stream/audiobookshelf/book1",
        params={"user": "testuser", "mt": mt},
    )
    assert resp.status_code == 200
    assert b"audio" in resp.content


def test_stream_abs_rejects_expired_media_token(client, upstream):
    mt, _exp = sign("testuser", now=time.time() - 7200)
    resp = client.get(
        "/api/media/stream/audiobookshelf/book1",
        params={"user": "testuser", "mt": mt},
    )
    assert resp.status_code == 403


def test_stream_abs_rejects_token_bound_to_another_user(client, upstream):
    """A token signed for testuser cannot be replayed as otheruser."""
    mt, _exp = sign("testuser")
    resp = client.get(
        "/api/media/stream/audiobookshelf/book1",
        params={"user": "otheruser", "mt": mt},
    )
    assert resp.status_code == 403
