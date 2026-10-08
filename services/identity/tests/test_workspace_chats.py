"""Workspace chats: a user's conversations in a workspace, kept turn by turn."""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.main import app, get_session, require_api_key
from services.identity.models import User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="jeremiah"))
        session.add(User(username="michele"))
        session.commit()
        yield session


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    _act_as("jeremiah")
    yield TestClient(app)
    app.dependency_overrides = {}


def test_a_chat_is_created_titled_by_its_first_question_and_keeps_its_parts(client):
    chat = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon"}).json()
    client.post(f"/api/users/me/workspace-chats/{chat['id']}/messages",
                json={"role": "user", "parts": [{"type": "text", "text": "Summarize   the\\nsermon notes"}]})
    client.post(f"/api/users/me/workspace-chats/{chat['id']}/messages", json={
        "role": "assistant",
        "parts": [{"type": "reasoning", "text": "look"}, {"type": "tool", "name": "WorkspaceSearchRequest", "status": "done"},
                  {"type": "text", "text": "Here it is."}],
        "meta": {"mode": "single_task"},
    })
    full = client.get(f"/api/users/me/workspace-chats/{chat['id']}").json()
    assert full["title"].startswith("Summarize the")
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert [p["type"] for p in full["messages"][1]["parts"]] == ["reasoning", "tool", "text"]
    assert full["messages"][1]["meta"] == {"mode": "single_task"}


def test_the_list_is_per_workspace_and_newest_first(client):
    a = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon", "title": "A"}).json()
    client.post("/api/users/me/workspace-chats", json={"workspace_id": "other", "title": "B"})
    c = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon", "title": "C"}).json()
    client.post(f"/api/users/me/workspace-chats/{a['id']}/messages", json={"role": "user", "parts": [{"type": "text", "text": "x"}]})
    titles = [c_["title"] for c_ in client.get("/api/users/me/workspace-chats", params={"workspace_id": "sermon"}).json()["chats"]]
    assert titles == ["A", "C"]
    assert c["id"]


def test_someone_elses_chat_does_not_exist_for_you(client):
    chat = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon"}).json()
    _act_as("michele")
    assert client.get(f"/api/users/me/workspace-chats/{chat['id']}").status_code == 404
    assert client.post(f"/api/users/me/workspace-chats/{chat['id']}/messages",
                       json={"role": "user", "parts": []}).status_code == 404
    assert client.delete(f"/api/users/me/workspace-chats/{chat['id']}").status_code == 404
    assert client.get("/api/users/me/workspace-chats", params={"workspace_id": "sermon"}).json()["chats"] == []


def test_rename_and_delete(client):
    chat = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon"}).json()
    assert client.patch(f"/api/users/me/workspace-chats/{chat['id']}", json={"title": "Advent"}).json()["title"] == "Advent"
    assert client.delete(f"/api/users/me/workspace-chats/{chat['id']}").status_code == 200
    assert client.get(f"/api/users/me/workspace-chats/{chat['id']}").status_code == 404


def test_bad_messages_are_refused(client):
    chat = client.post("/api/users/me/workspace-chats", json={"workspace_id": "sermon"}).json()
    url = f"/api/users/me/workspace-chats/{chat['id']}/messages"
    assert client.post(url, json={"role": "system", "parts": []}).status_code == 422
    assert client.post(url, json={"role": "user", "parts": "text"}).status_code == 422
    assert client.post(url, json={"role": "user", "parts": [{"type": "text", "text": "x" * 600_000}]}).status_code == 413
