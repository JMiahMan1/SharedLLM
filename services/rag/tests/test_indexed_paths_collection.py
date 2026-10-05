"""``/rag/indexed-paths`` must answer for the collection it was asked about.

The endpoint hardcoded ``collection_name='nextcloud_files'``, so it could not
report what any other collection held. The storage indexer needs it to compute
which of *its own* files disappeared upstream — a question that is meaningless
against a hardcoded collection, and wrong the moment a second provider exists.
"""
import asyncio
import json

import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.store import NumpyVecAdapter


def _install(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, 16)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    return conn


def _add(conn, item_id, collection, user_id, path):
    conn.execute(
        "INSERT OR REPLACE INTO rag_items (id, collection_name, user_id, content, metadata,"
        " created_at, indexed_at) VALUES (?,?,?,?,?,?,?)",
        (item_id, collection, user_id, "text", json.dumps({"path": path}), "t", "t"),
    )
    conn.commit()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wired(tmp_path):
    return _install(tmp_path)


def test_reports_paths_for_the_requested_collection(wired):
    _add(wired, "a", "nextcloud_files", "summers", "/books/one.epub")
    _add(wired, "b", "calibre_files", "summers", "/library/two.epub")
    paths = run(rag_main.get_indexed_paths("summers", "calibre_files"))["paths"]
    assert paths == ["/library/two.epub"]


def test_default_collection_is_preserved_for_existing_callers(wired):
    _add(wired, "a", "nextcloud_files", "summers", "/notes/a.md")
    paths = run(rag_main.get_indexed_paths("summers"))["paths"]
    assert paths == ["/notes/a.md"]


def test_users_do_not_see_each_others_paths(wired):
    _add(wired, "a", "nextcloud_files", "summers", "/notes/summers.md")
    _add(wired, "b", "nextcloud_files", "other", "/notes/other.md")
    assert run(rag_main.get_indexed_paths("other"))["paths"] == ["/notes/other.md"]


def test_unknown_collection_is_empty_not_an_error(wired):
    _add(wired, "a", "nextcloud_files", "summers", "/notes/a.md")
    assert run(rag_main.get_indexed_paths("summers", "nothing_here"))["paths"] == []


def test_rows_without_a_path_are_discarded(wired):
    conn = wired
    conn.execute(
        "INSERT OR REPLACE INTO rag_items (id, collection_name, user_id, content, metadata,"
        " created_at, indexed_at) VALUES (?,?,?,?,?,?,?)",
        ("x", "nextcloud_files", "summers", "text", json.dumps({"other": 1}), "t", "t"),
    )
    conn.commit()
    assert run(rag_main.get_indexed_paths("summers"))["paths"] == []