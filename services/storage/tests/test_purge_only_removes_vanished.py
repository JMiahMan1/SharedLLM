"""Purging must remove vanished files, never live ones.

The index used to purge the entire collection before repopulating it. Any
failure partway through the repopulation left the index permanently smaller,
with no copy of the removed chunks anywhere. Syncing is already idempotent
(the per-chunk id is derived from user + path + index), so the only thing a
purge is needed for is dropping chunks whose source file is gone upstream.
"""
import asyncio
import logging

import pytest

from services.storage.main import _purge_stale_paths

log = logging.getLogger(__name__)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class FakeClient:
    def __init__(self, listed=None, list_status=200, post_status=200):
        self.listed = listed or []
        self.list_status = list_status
        self.post_status = post_status
        self.gets = []
        self.purges = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, params or {}))
        return FakeResponse(self.list_status, {"status": "SUCCESS", "paths": self.listed})

    async def post(self, url, json=None, headers=None, timeout=None):
        self.purges.append((url, json or {}))
        return FakeResponse(self.post_status)


def run(client, collection, user, scanned):
    return asyncio.run(_purge_stale_paths(client, collection, user, scanned))


def test_only_vanished_paths_are_purged():
    client = FakeClient(listed=["/a.md", "/gone.md", "/b.md"])
    run(client, "nextcloud_files", "summers", {"/a.md", "/b.md"})
    purged = sorted(body["filter"]["path"] for _, body in client.purges)
    assert purged == ["/gone.md"]


def test_purge_scopes_to_the_right_user_and_collection():
    client = FakeClient(listed=["/gone.md"])
    run(client, "nextcloud_files", "summers", set())
    url, body = client.purges[0]
    assert url.endswith("/rag/purge/nextcloud_files")
    assert body["user_id"] == "summers"
    assert body["filter"] == {"path": "/gone.md"}


def test_listing_uses_the_indexed_paths_endpoint_not_a_bare_collection_purge():
    client = FakeClient(listed=[])
    run(client, "nextcloud_files", "summers", {"/a.md"})
    url, params = client.gets[0]
    assert url.endswith("/rag/indexed-paths")
    assert params["collection_name"] == "nextcloud_files"
    assert params["user_id"] == "summers"


def test_nothing_purged_when_every_still_present():
    client = FakeClient(listed=["/a.md", "/b.md"])
    run(client, "nextcloud_files", "summers", {"/a.md", "/b.md"})
    assert client.purges == []


def test_live_chunks_survive_a_failed_listing():
    """A bookkeeping failure must never escalate into deleting live chunks."""
    client = FakeClient(list_status=500)
    run(client, "nextcloud_files", "summers", {"/a.md"})
    assert client.purges == []


def test_live_chunks_survive_a_raising_client():
    class Exploding(FakeClient):
        async def get(self, *a, **kw):
            raise RuntimeError("connection reset")

    client = Exploding()
    run(client, "nextcloud_files", "summers", {"/a.md"})
    assert client.purges == []


def test_empty_index_purges_nothing():
    client = FakeClient(listed=[])
    run(client, "nextcloud_files", "summers", set())
    assert client.purges == []


def test_one_failing_purge_does_not_abort_the_remainder():
    class Flaky(FakeClient):
        async def post(self, url, json=None, headers=None, timeout=None):
            self.purges.append((url, json or {}))
            status = self.post_status if json.get("filter", {}).get("path") != "/one.md" else 500
            return FakeResponse(status)

    client = Flaky(listed=["/one.md", "/two.md"])
    run(client, "nextcloud_files", "summers", set())
    assert len(client.purges) == 2