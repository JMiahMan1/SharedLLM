"""Profile pictures (built-in or uploaded) and Skylight status for every user."""
import base64

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.crypto import encrypt
from services.identity.main import app, get_session, require_api_key
from services.identity.models import User, UserAvatarImage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="default", display_name="Admin", is_admin=True, is_system_default=True))
        session.add(User(id=2, username="michele", display_name="Michele"))
        session.commit()
        yield session


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    _act_as("michele")
    yield TestClient(app)
    app.dependency_overrides = {}


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_a_built_in_picture_is_saved(client):
    body = client.put("/api/users/me/avatar", json={"preset": "balaams-donkey"}).json()
    assert body["avatar"] == "preset:balaams-donkey"
    assert client.put("/api/users/me/avatar", json={"preset": "../etc"}).status_code == 422


def test_an_uploaded_photo_is_kept_and_served(client, session):
    body = client.post("/api/users/me/avatar/image", json={"data_url": _data_url(PNG)}).json()
    assert body["avatar"].startswith("upload:")
    img = client.get("/api/users/michele/avatar/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == PNG


def test_only_real_images_are_kept(client):
    bad = client.post("/api/users/me/avatar/image", json={"data_url": _data_url(b"<svg onload=alert(1)>", "image/png")})
    assert bad.status_code == 415
    big = client.post("/api/users/me/avatar/image", json={"data_url": _data_url(PNG + b"\x00" * 1_600_000)})
    assert big.status_code == 413
    assert client.post("/api/users/me/avatar/image", json={"data_url": "not a data url"}).status_code == 422


def test_removing_the_picture_deletes_the_photo(client, session):
    client.post("/api/users/me/avatar/image", json={"data_url": _data_url(PNG)})
    assert client.delete("/api/users/me/avatar").json()["avatar"] is None
    session.expire_all()
    assert session.get(UserAvatarImage, "michele") is None
    assert client.get("/api/users/michele/avatar/image").status_code == 404


def test_skylight_reads_not_set_up_until_the_household_account_is(client, session):
    assert client.get("/api/users/me").json()["skylight_status"] == "not_set_up"
    admin = session.get(User, 1)
    admin.skylight_email, admin.skylight_pass_enc = "family@example.com", encrypt("pw")
    session.add(admin)
    session.commit()
    assert client.get("/api/users/me").json()["skylight_status"] == "connected"
    client.patch("/api/users/me", json={"skylight_enabled": False})
    assert client.get("/api/users/me").json()["skylight_status"] == "off"


def test_a_default_chat_is_remembered_and_cleared(client):
    assert client.patch("/api/users/me", json={"chat_default_token": "abc123"}).json()["chat_default_token"] == "abc123"
    assert client.get("/api/users/me").json()["chat_default_token"] == "abc123"
    # Blank never erases (see _apply_user_update); clearing is explicit.
    assert client.patch("/api/users/me", json={"chat_default_token": ""}).json()["chat_default_token"] == "abc123"
    cleared = client.patch("/api/users/me", json={"clear_fields": ["chat_default_token"]}).json()
    assert cleared["chat_default_token"] is None


def test_the_family_directory_shows_names_and_pictures_only(client, session):
    client.put("/api/users/me/avatar", json={"preset": "jonahs-whale"})
    people = {p["username"]: p for p in client.get("/api/users/family-directory").json()}
    assert people["michele"]["avatar"] == "preset:jonahs-whale"
    assert set(people["michele"]) == {"username", "display_name", "nextcloud_user", "avatar"}
    assert "default" in people
