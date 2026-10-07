"""Where a reader left off in a workspace, saved against their own account.

The blob is written by the UI and only ever read back by the same account, so
these tests are about ownership and about what happens when a stored row is not
the shape it should be -- a corrupt row must not take the whole list down.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User, UserWorkspaceSession


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="actor")
def actor_fixture() -> dict:
    """Whoever the next request is made as, so a second reader can be tried."""
    return {}


@pytest.fixture(name="client")
def client_fixture(session: Session, actor: dict):
    main.engine = session.bind
    assert main.engine is not None
    SQLModel.metadata.create_all(main.engine)

    from services.identity.seed import seed_from_env

    seed_from_env(session, force=True)

    owner = session.exec(select(User).where(User.username == "default")).first()
    assert owner is not None
    actor["user"] = owner

    app.dependency_overrides[require_api_key] = lambda: actor["user"]
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def another_user(session: Session, username: str = "michele") -> User:
    user = User(username=username, display_name=username.title())
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def a_session(path: str = "Bible Study", saved_at: int = 1000) -> dict:
    return {
        "view": "git",
        "currentPath": path,
        "terminalOpen": True,
        "terminalPosition": "sidebar",
        "terminalHeight": 320,
        "activeTab": "sermon.md",
        "tabs": [
            {"path": "sermon.md", "kind": "markdown", "dirty": False},
            {
                "path": "notes.txt",
                "kind": "text",
                "dirty": True,
                "content": "half a thought",
                "language": "plaintext",
            },
        ],
        "savedAt": saved_at,
    }


def test_a_workspace_opens_where_it_was_left(client: TestClient):
    saved = a_session()
    resp = client.put("/api/users/me/workspace-sessions/sermon", json=saved)
    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == "sermon"

    resp = client.get("/api/users/me/workspace-sessions")
    assert resp.status_code == 200
    assert resp.json()["sessions"]["sermon"] == saved


def test_an_unsaved_buffer_travels_with_the_session(client: TestClient):
    client.put("/api/users/me/workspace-sessions/sermon", json=a_session())

    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    dirty = [t for t in sessions["sermon"]["tabs"] if t["path"] == "notes.txt"][0]
    assert dirty["dirty"] is True
    assert dirty["content"] == "half a thought"


def test_saving_again_replaces_the_earlier_copy(client: TestClient, session: Session):
    client.put("/api/users/me/workspace-sessions/sermon", json=a_session(saved_at=1000))
    client.put(
        "/api/users/me/workspace-sessions/sermon",
        json=a_session(path="Bible Study/Week 2", saved_at=2000),
    )

    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    assert sessions["sermon"]["currentPath"] == "Bible Study/Week 2"
    assert sessions["sermon"]["savedAt"] == 2000
    rows = session.exec(select(UserWorkspaceSession)).all()
    assert len(rows) == 1


def test_one_readers_sessions_are_not_anothers(client: TestClient, session: Session, actor: dict):
    client.put("/api/users/me/workspace-sessions/sermon", json=a_session())

    actor["user"] = another_user(session)
    assert client.get("/api/users/me/workspace-sessions").json()["sessions"] == {}

    client.put("/api/users/me/workspace-sessions/home-work", json=a_session(path="."))
    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    assert list(sessions) == ["home-work"]


def test_a_reader_with_nothing_saved_gets_an_empty_map(client: TestClient):
    body = client.get("/api/users/me/workspace-sessions").json()
    assert body["status"] == "SUCCESS"
    assert body["sessions"] == {}


def test_forgetting_a_workspace_removes_only_that_one(client: TestClient):
    client.put("/api/users/me/workspace-sessions/sermon", json=a_session())
    client.put("/api/users/me/workspace-sessions/home-work", json=a_session())

    resp = client.delete("/api/users/me/workspace-sessions/sermon")
    assert resp.status_code == 200

    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    assert list(sessions) == ["home-work"]


def test_an_unreadable_row_is_skipped_rather_than_breaking_the_list(
    client: TestClient, session: Session, actor: dict
):
    client.put("/api/users/me/workspace-sessions/sermon", json=a_session())
    session.add(
        UserWorkspaceSession(username=actor["user"].username, workspace_id="broken", data="not json")
    )
    session.commit()

    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    assert list(sessions) == ["sermon"]


def test_a_stored_row_is_handed_back_verbatim(client: TestClient, session: Session, actor: dict):
    session.add(
        UserWorkspaceSession(
            username=actor["user"].username,
            workspace_id="hand-written",
            data=json.dumps({"view": "tools", "extra": "kept"}),
        )
    )
    session.commit()

    sessions = client.get("/api/users/me/workspace-sessions").json()["sessions"]
    assert sessions["hand-written"] == {"view": "tools", "extra": "kept"}
