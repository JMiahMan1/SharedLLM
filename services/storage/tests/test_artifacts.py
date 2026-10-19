"""Artifacts: quota, ownership, undo window, and signed streaming."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.storage import artifacts
from services.storage.artifacts_api import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ARTIFACT_LOCAL_QUOTA_BYTES", str(2 * 1024 * 1024))
    monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("ARTIFACT_SIGNING_KEY", "signing-secret")
    artifacts.reset_for_tests()
    artifacts.init_db()

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client
    artifacts.reset_for_tests()


def _headers(user: str = "alice", *, admin: bool = False) -> dict[str, str]:
    return {
        "X-Internal-Secret": "test-secret",
        "X-User": user,
        "X-Admin": "true" if admin else "false",
    }


def _create(client: TestClient, *, name="note.txt", content=b"hello", kind="document", user="alice"):
    response = client.post(
        "/artifacts",
        headers=_headers(user),
        json={
            "name": name,
            "kind": kind,
            "mime_type": "text/plain",
            "content_b64": base64.b64encode(content).decode(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_and_signed_stream(client):
    created = _create(client, name="hello.txt", content=b"hello artifact")
    assert created["owner_user"] == "alice"
    assert created["size"] == len(b"hello artifact")
    assert created["metadata"] == {}
    assert created["download_url"].startswith("/artifacts/")
    assert "token=" in created["download_url"]

    listing = client.get("/artifacts", headers=_headers())
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    token_url = client.get(
        f"/artifacts/{created['id']}/download", headers=_headers()
    ).json()["url"]
    streamed = client.get(token_url)
    assert streamed.status_code == 200
    assert streamed.content == b"hello artifact"

    ranged = client.get(token_url, headers={"Range": "bytes=0-4"})
    assert ranged.status_code == 206
    assert ranged.content == b"hello"


def test_stream_encodes_unsafe_filenames(client):
    created = _create(client, name='report "final"\n.txt', content=b"x")
    token_url = client.get(
        f"/artifacts/{created['id']}/download", headers=_headers()
    ).json()["url"]
    streamed = client.get(token_url)
    assert streamed.status_code == 200
    disposition = streamed.headers["content-disposition"]
    assert "\n" not in disposition
    assert "filename*=utf-8''" in disposition
    assert "%22final%22%0A.txt" in disposition


def test_another_user_cannot_see_or_read_an_artifact(client):
    created = _create(client, user="alice")
    assert client.get(f"/artifacts/{created['id']}", headers=_headers("bob")).status_code == 404
    assert client.delete(f"/artifacts/{created['id']}", headers=_headers("bob")).status_code == 404
    assert client.get("/artifacts", headers=_headers("bob")).json()["total"] == 0


def test_soft_delete_is_undoable_and_expires(client):
    created = _create(client)
    deleted = client.delete(f"/artifacts/{created['id']}", headers=_headers())
    assert deleted.status_code == 200
    assert deleted.json()["hard"] is False
    assert client.get("/artifacts", headers=_headers()).json()["total"] == 0

    restored = client.post(f"/artifacts/{created['id']}/restore", headers=_headers())
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert client.get("/artifacts", headers=_headers()).json()["total"] == 1

    client.delete(f"/artifacts/{created['id']}", headers=_headers())
    # Simulate the 24-hour window having passed.
    with artifacts._session() as session:
        row = session.get(artifacts.Artifact, created["id"])
        row.deleted_at = artifacts._now() - artifacts.timedelta(hours=25)
        session.add(row)
        session.commit()
    assert client.post(f"/artifacts/{created['id']}/restore", headers=_headers()).status_code == 410
    assert artifacts.purge_expired() == 1
    assert client.get(f"/artifacts/{created['id']}", headers=_headers()).status_code == 404


def test_hard_delete_requires_the_exact_phrase(client):
    created = _create(client, name="take me.txt")
    wrong = client.request(
        "DELETE",
        f"/artifacts/{created['id']}?hard=true",
        headers=_headers(),
        json={"confirm_phrase": "delete"},
    )
    assert wrong.status_code == 400
    good = client.request(
        "DELETE",
        f"/artifacts/{created['id']}?hard=true",
        headers=_headers(),
        json={"confirm_phrase": "delete take me.txt"},
    )
    assert good.status_code == 200
    assert client.get(f"/artifacts/{created['id']}", headers=_headers()).status_code == 404


def test_quota_blocks_a_second_large_artifact(client):
    _create(client, name="first.bin", content=b"a" * (1200 * 1024))
    second = client.post(
        "/artifacts",
        headers=_headers(),
        json={
            "name": "second.bin",
            "kind": "other",
            "content_b64": base64.b64encode(b"b" * (1200 * 1024)).decode(),
        },
    )
    assert second.status_code == 413
    assert "Cannot store artifact" in second.json()["detail"]

    fit = client.post("/artifacts/check-fit", headers=_headers(), json={"size": 1200 * 1024})
    assert fit.status_code == 200
    assert fit.json()["fits"] is False


def test_usage_reports_the_owner_and_admins_can_see_everyone(client):
    _create(client, user="alice", content=b"12345")
    _create(client, user="bob", content=b"1234567890")

    alice = client.get("/storage/usage", headers=_headers("alice")).json()
    assert alice["user"] == "alice"
    assert alice["used"] == 5
    assert alice["count"] == 1

    assert client.get("/storage/usage/system", headers=_headers("alice")).status_code == 403
    system = client.get("/storage/usage/system", headers=_headers("alice", admin=True))
    assert system.status_code == 200
    assert system.json()["total"] == 15
    assert {row["user"] for row in system.json()["users"]} == {"alice", "bob"}


def test_signed_stream_rejects_a_tampered_or_expired_token(client):
    created = _create(client)
    token_url = client.get(
        f"/artifacts/{created['id']}/download", headers=_headers()
    ).json()["url"]
    tampered = token_url[:-1] + ("0" if token_url[-1] != "0" else "1")
    assert client.get(tampered).status_code == 403
    assert client.get(f"/artifacts/{created['id']}/stream").status_code == 403
