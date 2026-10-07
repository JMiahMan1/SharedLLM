"""Maintenance: report first, purge second, retention from config only.

The user's ask: "is there a way to remove duplicate entries in RAG or
entries that are no longer valid? A clean up, or in some cases a purging of
expired data?" -- with house rules layered on top: report before deleting,
and never expire on a guessed schedule (``rag_retention_days`` blank means
keep everything).

Duplicate semantics, deliberately narrow: identical content only counts as a
duplicate when the rows carry no ``path`` metadata. File chunks are
path-identified, so the same text under two paths is two files, not one row
stored twice -- deleting the "twin" would make a real file unsearchable.
The survivor of a real duplicate group is the most-used row, then the
oldest, so citations and accumulated ``usage_count`` keep the history.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from services.rag import db
from services.rag import main as rag_main
from services.rag.main import require_internal
from services.rag.store import NumpyVecAdapter

DIM = 16
# Real now: the endpoint ages rows against time.time(), so a fixed far-future
# anchor would make every "old" row look fresh.
NOW = int(time.time())
DAY = 86_400


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
                row.get("collection", "system_learnings"),
                row.get("user", "default"),
                row["content"],
                json.dumps(row.get("meta", {})),
                row.get("created", NOW - 10 * DAY),
                "2026-01-01T00:00:00Z",
                row.get("usage", 0),
            ),
        )
    conn.commit()


def _rows():
    return {
        r["id"]
        for r in rag_main._conn().execute("SELECT id FROM rag_items ORDER BY id").fetchall()
    }


def _run(client, payload):
    return client.post("/rag/maintenance", json=payload)


@pytest.fixture
def client(tmp_path):
    _install(tmp_path)
    c = TestClient(rag_main.app)
    c.app.dependency_overrides[require_internal] = lambda: None
    try:
        yield c
    finally:
        c.app.dependency_overrides.clear()


def test_report_counts_duplicates_but_deletes_nothing(client):
    _seed(
        [
            {"id": "l:1", "content": "always cite the source"},
            {"id": "l:2", "content": "always cite the source"},
        ]
    )
    resp = _run(client, {"mode": "report", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "report"
    assert body["collections"]["system_learnings"]["duplicate_groups"] == 1
    assert body["collections"]["system_learnings"]["duplicate_rows"] == 1
    assert body["totals"]["removable"] == 1
    assert "removed" not in body
    assert _rows() == {"l:1", "l:2"}, "a report must not delete"


def test_purge_keeps_the_most_used_survivor(client):
    _seed(
        [
            {"id": "l:new", "content": "always cite the source", "usage": 0, "created": NOW - DAY},
            {"id": "l:old", "content": "always cite the source", "usage": 0, "created": NOW - 40 * DAY},
            {"id": "l:used", "content": "always cite the source", "usage": 9, "created": NOW - 20 * DAY},
        ]
    )
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 2
    assert _rows() == {"l:used"}, "the accumulated usage_count must survive on one row"


def test_identical_text_under_a_path_is_two_files_not_a_duplicate(client):
    """Both files must stay searchable; dedupe must not touch them."""
    _seed(
        [
            {"id": "f:a", "collection": "nextcloud_files", "content": "same text", "meta": {"path": "/x/copy.txt"}},
            {"id": "f:b", "collection": "nextcloud_files", "content": "same text", "meta": {"path": "/y/copy.txt"}},
        ]
    )
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 0
    assert _rows() == {"f:a", "f:b"}


def test_empty_content_rows_are_invalid_and_removed(client):
    _seed([{"id": "n:1", "content": ""}, {"id": "n:2", "content": "   "}, {"id": "k:1", "content": "keep me"}])
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 2
    assert _rows() == {"k:1"}


def test_rows_in_a_collection_with_no_retention_never_expire(client, monkeypatch):
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", '{"telemetry_alerts": 30}')
    _seed([{"id": "s:old", "collection": "system_learnings", "content": "ancient lesson", "created": NOW - 400 * DAY}])
    resp = _run(client, {"mode": "report", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["collections"]["system_learnings"]["expired_rows"] == 0


def test_an_expired_row_is_expired_only_when_the_setting_says_so(client, monkeypatch):
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", '{"telemetry_alerts": 30}')
    _seed(
        [
            {"id": "t:old", "collection": "telemetry_alerts", "content": "old alert", "created": NOW - 60 * DAY},
            {"id": "t:fresh", "collection": "telemetry_alerts", "content": "fresh alert", "created": NOW - 5 * DAY},
        ]
    )
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    assert _rows() == {"t:fresh"}


def test_a_blank_retention_setting_expires_nothing(client, monkeypatch):
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", "")
    _seed([{"id": "t:old", "collection": "telemetry_alerts", "content": "old alert", "created": NOW - 4000 * DAY}])
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 0
    assert resp.json()["retention"] == {}
    assert _rows() == {"t:old"}


def test_a_malformed_retention_setting_fails_loudly(client, monkeypatch):
    """A typo like \"30d\" must not silently mean keep-forever."""
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", "30d")
    _seed([{"id": "t:1", "collection": "telemetry_alerts", "content": "alert"}])
    resp = _run(client, {"mode": "report", "user_id": "default"})
    assert resp.status_code == 500
    assert "rag_retention_days" in resp.json()["detail"]


def test_a_retention_value_below_one_day_is_refused(client, monkeypatch):
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", '{"telemetry_alerts": 0}')
    _seed([{"id": "t:1", "collection": "telemetry_alerts", "content": "alert"}])
    resp = _run(client, {"mode": "report", "user_id": "default"})
    assert resp.status_code == 500
    assert "telemetry_alerts" in resp.json()["detail"]


def test_an_unknown_mode_is_refused(client):
    _seed([{"id": "k:1", "content": "keep"}])
    resp = _run(client, {"mode": "wipe", "user_id": "default"})
    assert resp.status_code == 400
    assert "report" in resp.json()["detail"]
    assert _rows() == {"k:1"}


def test_an_unknown_collection_is_refused_not_ignored(client):
    _seed([{"id": "k:1", "content": "keep"}])
    resp = _run(client, {"mode": "purge", "user_id": "default", "collections": ["nope_files"]})
    assert resp.status_code == 400
    assert "nope_files" in resp.json()["detail"]
    assert _rows() == {"k:1"}, "an ignored typo would purge nothing silently"


def test_a_scoped_purge_touches_only_the_named_collection(client, monkeypatch):
    monkeypatch.setattr("services.config.RAG_RETENTION_DAYS", "")
    _seed(
        [
            {"id": "d:1", "collection": "telemetry_alerts", "content": "dup", "created": NOW - DAY},
            {"id": "d:2", "collection": "telemetry_alerts", "content": "dup", "created": NOW - DAY},
            {"id": "l:1", "collection": "system_learnings", "content": "dup", "created": NOW - DAY},
            {"id": "l:2", "collection": "system_learnings", "content": "dup", "created": NOW - DAY},
        ]
    )
    resp = _run(client, {"mode": "purge", "user_id": "default", "collections": ["telemetry_alerts"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    assert _rows() == {"d:1", "l:1", "l:2"}, "the un-named collection must be untouched"


def test_the_report_says_how_many_rows_a_purge_would_remove(client):
    _seed(
        [
            {"id": "a", "content": "x", "created": NOW - DAY},
            {"id": "b", "content": "x", "created": NOW - DAY},
            {"id": "c", "content": ""},
        ]
    )
    report = _run(client, {"mode": "report", "user_id": "default"}).json()
    purge = _run(client, {"mode": "purge", "user_id": "default"}).json()
    assert purge["removed"] == report["totals"]["removable"] == 2


def test_rows_owned_by_another_user_are_never_touched(client):
    _seed(
        [
            {"id": "mine", "content": "dup"},
            {"id": "theirs", "content": "dup", "user": "alice"},
        ]
    )
    resp = _run(client, {"mode": "purge", "user_id": "default"})
    assert resp.status_code == 200
    assert resp.json()["removed"] == 0
    assert _rows() == {"mine", "theirs"}
