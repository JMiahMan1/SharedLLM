"""BUG-21 / P2-T12: MA playlists/recent/browse must not 500 on ClientError.

When the execution service is unreachable (aiohttp.ClientError), these
endpoints must catch it and return HTTP 502 with {status: "ERROR", error}.
"""
import re

import aiohttp
import pytest

from conftest_media import mock_upstream  # noqa: F401  (fixtures come via conftest)


EXEC_PATHS = {
    "/api/media/music-assistant/playlists": "playlists",
    "/api/media/music-assistant/recent": "recent",
    "/api/media/music-assistant/browse": "browse",
}


def _register_upstream_down(upstream, exec_base: str, subpath: str) -> None:
    """Make the upstream call raise ClientError (execution svc unreachable)."""
    pattern = re.compile(re.escape(f"{exec_base}/{subpath}") + r".*")
    upstream.get(pattern, exception=aiohttp.ClientError("execution svc down"))


@pytest.mark.parametrize("endpoint,subpath", sorted(EXEC_PATHS.items()))
def test_client_error_returns_502_error_status(client, upstream, endpoint, subpath):
    from services.gateway.main import EXECUTION_SVC

    _register_upstream_down(
        upstream, f"{EXECUTION_SVC}/execute/media/music-assistant", subpath
    )

    resp = client.get(endpoint)

    assert resp.status_code == 502, (
        f"{endpoint}: expected 502 on ClientError, got {resp.status_code}"
    )
    body = resp.json()
    assert body.get("status") == "ERROR", f"{endpoint}: body={body}"
    assert "error" in body, f"{endpoint}: body={body}"
