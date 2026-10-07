"""The purge landmine: a filter that must actually filter.

Two shapes of the same bug made every scoped purge delete far more than it
named:

1. The legacy ``GET /rag/purge/{collection}?filter=...`` *required* a filter
   and then ignored it -- deleting every row the caller owned in that
   collection while they watched a scoped request succeed.
2. The gateway's proxy sent the filter dict as the bare request body, so
   RAG's ``payload.get("user_id")`` defaulted to ``"default"`` and
   ``payload.get("filter")`` found nothing: a UI purge scoped to one path
   became a whole-collection delete of the default user's rows.

Both purge forms now resolve ids through the single ``_purge_ids`` builder,
so the two cannot drift apart again. These tests seed ``rag_items`` directly
(the rows carry no vectors; ``_delete_items`` tolerates that) and drive the
real HTTP handlers.
"""

import json

import pytest
from fastapi.testclient import TestClient

from services.rag import main as rag_main
from services.rag import db
from services.rag.main import require_internal
from services.rag.store import NumpyVecAdapter

DIM = 16


def _install(tmp_path):
    # TestClient runs handlers on a worker thread; production creates this
    # connection on the same loop thread the async handlers run on.
    conn = db.get_db_connection(str(tmp_path / "rag.db"), check_same_thread=False)
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()
    rag_main.EMBEDDING_DIM = DIM
    return conn


def _seed(rows):
    conn = rag_main._conn()
    for row in rows:
        conn.execute(
            "INSERT INTO rag_items (id, collection_name, user_id, content, metadata,"
            " created_at, indexed_at, usage_count)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row.get("collection", "calibre_files"),
                row.get("user", "default"),
                row["content"],
                json.dumps(row.get("meta", {})),
                row.get("created", 1700000000),
                "2026-01-01T00:00:00Z",
                row.get("usage", 0),
            ),
        )
    conn.commit()


def _remaining():
    return {
        r["id"]
        for r in rag_main._conn()
        .execute("SELECT id FROM rag_items ORDER BY id")
        .fetchall()
    }


@pytest.fixture
def client(tmp_path):
    _install(tmp_path)
    c = TestClient(rag_main.app)
    c.app.dependency_overrides[require_internal] = lambda: None
    try:
        yield c
    finally:
        c.app.dependency_overrides.clear()


def _secret_headers():
    return {"X-Internal-Secret": rag_main.INTERNAL_SECRET}


def test_a_legacy_get_filter_now_deletes_only_what_it_names(client):
    _seed(
        [
            {"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}},
            {"id": "f:b", "content": "beta", "meta": {"path": "/b.txt"}},
            {"id": "f:c", "content": "gamma", "meta": {"path": "/c.txt"}},
        ]
    )
    resp = client.get(
        "/rag/purge/calibre_files",
        params={"user_id": "default", "filter": json.dumps({"path": "/a.txt"})},
        headers=_secret_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    assert _remaining() == {"f:b", "f:c"}, "the filter was ignored and siblings died"


def test_get_without_a_filter_is_still_refused_and_deletes_nothing(client):
    _seed([{"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}}])
    resp = client.get(
        "/rag/purge/calibre_files",
        params={"user_id": "default"},
        headers=_secret_headers(),
    )
    assert resp.status_code == 403
    assert _remaining() == {"f:a"}


def test_get_with_a_malformed_filter_is_refused_before_any_delete(client):
    _seed(
        [
            {"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}},
            {"id": "f:b", "content": "beta", "meta": {"path": "/b.txt"}},
        ]
    )
    resp = client.get(
        "/rag/purge/calibre_files",
        params={"user_id": "default", "filter": "path=/a.txt"},
        headers=_secret_headers(),
    )
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]
    assert _remaining() == {"f:a", "f:b"}, "a parse failure must never mean 'delete all'"


def test_get_with_a_json_array_filter_is_refused(client):
    _seed([{"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}}])
    resp = client.get(
        "/rag/purge/calibre_files",
        params={"user_id": "default", "filter": json.dumps(["/a.txt"])},
        headers=_secret_headers(),
    )
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["detail"]
    assert _remaining() == {"f:a"}


def test_post_with_a_filter_purges_only_matching_rows(client):
    """Storage's stale-path purge depends on this contract."""
    _seed(
        [
            {"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}},
            {"id": "f:b", "content": "beta", "meta": {"path": "/b.txt"}},
        ]
    )
    resp = client.post(
        "/rag/purge/calibre_files",
        json={"user_id": "default", "filter": {"path": "/a.txt"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    assert _remaining() == {"f:b"}


def test_post_with_an_empty_filter_purges_one_users_collection_only(client):
    _seed(
        [
            {"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}},
            {"id": "f:b", "content": "beta", "user": "alice", "meta": {"path": "/b.txt"}},
        ]
    )
    resp = client.post(
        "/rag/purge/calibre_files",
        json={"user_id": "default", "filter": {}},
    )
    assert resp.status_code == 200, resp.text
    assert _remaining() == {"f:b"}, "another owner's rows must survive a purge"


def test_a_non_object_filter_on_post_is_refused(client):
    _seed([{"id": "f:a", "content": "alpha", "meta": {"path": "/a.txt"}}])
    resp = client.post(
        "/rag/purge/calibre_files",
        json={"user_id": "default", "filter": "path"},
    )
    assert resp.status_code == 400
    assert _remaining() == {"f:a"}


def test_both_purge_forms_share_one_where_builder(client):
    """The drift that produced this file: two handlers, two behaviours."""
    import inspect

    post_src = inspect.getsource(rag_main.purge_collection_endpoint)
    get_src = inspect.getsource(rag_main.purge_rag_collection)
    assert "_purge_ids(" in post_src
    assert "_purge_ids(" in get_src
    for src in (post_src, get_src):
        assert "SELECT id FROM rag_items" not in src, (
            "a purge form grew its own WHERE builder; the filter can drift again"
        )
