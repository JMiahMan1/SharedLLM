"""Identity's part of the account export (docs/ACCOUNT_EXPORT.md).

Only the internal secret opens these routes; an export holds only that
person's rows and never a password, key or token; a restore merges by stable
id, never writes into another account, never overwrites newer data without
being asked, and never restores the rules a parent set.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.common.account_data import is_secret_name
from services.identity.crypto import encrypt
from services.identity.main import app
from services.identity.models import (
    APIKey,
    AuditEvent,
    ScreenRules,
    User,
    UserThemeSetting,
    UserWidget,
    WorkspaceChat,
    WorkspaceChatMessage,
)

SECRET = {"X-Internal-Secret": "test-secret"}


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="admin", display_name="Admin", is_admin=True))
        sam = User(
            username="sam",
            display_name="Sam Example",
            password_hash="pbkdf2$not-a-real-hash",
            api_key_hash="deadbeef",
            nextcloud_url="https://cloud.example.test",
            nextcloud_user="sam",
            nextcloud_pass_enc=encrypt("nc-app-password"),
            ha_token_enc=encrypt("ha-long-lived-token"),
            push_topic="jarvis-sam-secret-topic",
        )
        session.add(sam)
        session.add(User(username="kid", display_name="Kid", family_role="child"))
        session.add(User(username="pat", display_name="Pat"))
        session.commit()
        session.refresh(sam)
        session.add(APIKey(key_value="k-123", key_hash="h", user_id=sam.id, label="phone"))
        session.add(UserThemeSetting(username="sam", data=json.dumps({"theme_id": "forest"})))
        session.add(UserThemeSetting(username="pat", data=json.dumps({"theme_id": "pat-only"})))
        session.add(UserWidget(username="sam", widget_key="weather", order_index=1, updated_at=1000))
        session.add(UserWidget(username="pat", widget_key="weather", order_index=9, updated_at=1000))
        session.add(WorkspaceChat(id="c1", workspace_id="w", username="sam", title="Plan", updated_at="2026-01-01T00:00:00"))
        session.add(WorkspaceChat(id="p1", workspace_id="w", username="pat", title="Pat's", updated_at="2026-01-01T00:00:00"))
        session.add(WorkspaceChatMessage(chat_id="c1", role="user", parts='[{"type":"text","text":"hello"}]'))
        session.add(WorkspaceChatMessage(chat_id="p1", role="user", parts='[{"type":"text","text":"pat secret"}]'))
        session.add(ScreenRules(username="kid", data=json.dumps({"daily_limit_min": 60})))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    yield TestClient(app)
    app.dependency_overrides = {}


def _export(client, who="sam"):
    resp = client.get(f"/api/internal/account/export/{who}", headers=SECRET)
    assert resp.status_code == 200, resp.text
    return resp.json()["collections"]


def test_routes_need_the_internal_secret(client):
    assert client.get("/api/internal/account/export/sam").status_code == 403
    assert client.get("/api/internal/account/export/sam", headers={"X-Internal-Secret": "wrong"}).status_code == 403
    assert client.post("/api/internal/account/import/sam", json={"collections": {}}).status_code == 403
    assert client.get("/api/internal/account/may-manage?actor=sam&subject=sam").status_code == 403


def test_export_holds_only_the_persons_rows_and_no_secrets(client):
    data = _export(client)
    text = json.dumps(data)
    for leaked in ("nc-app-password", "ha-long-lived-token", "pbkdf2$", "deadbeef", "k-123", "secret-topic", "pat-only", "pat secret"):
        assert leaked not in text
    for rows in data.values():
        for row in rows:
            assert not [k for k in row if is_secret_name(k)]
    assert data["profile"][0]["username"] == "sam"
    assert data["profile"][0]["nextcloud_user"] == "sam"
    assert "api_keys" not in data
    assert [w["widget_key"] for w in data["dashboard_widgets"]] == ["weather"]
    assert [c["id"] for c in data["workspace_chats"]] == ["c1"]
    assert len(data["workspace_chat_messages"]) == 1


def test_unknown_person_is_404(client):
    assert client.get("/api/internal/account/export/nobody", headers=SECRET).status_code == 404


def test_restore_dry_run_counts_and_writes_nothing(client, session):
    data = _export(client)
    data["theme"][0]["data"] = json.dumps({"theme_id": "ocean"})
    data["dashboard_widgets"].append({"id": 999, "username": "sam", "widget_key": "steps", "order_index": 2, "updated_at": 5})
    resp = client.post("/api/internal/account/import/sam", headers=SECRET, json={"collections": data, "dry_run": True})
    assert resp.status_code == 200
    out = resp.json()["collections"]
    # The theme has no time to compare: it is a conflict, never silently overwritten.
    assert out["theme"]["conflict"] == 1
    assert out["dashboard_widgets"]["add"] == 1
    assert out["dashboard_widgets"]["unchanged"] == 1
    session.expire_all()
    assert json.loads(session.get(UserThemeSetting, "sam").data)["theme_id"] == "forest"
    assert len(session.exec(select(UserWidget).where(UserWidget.username == "sam")).all()) == 1


def test_restore_merges_newer_and_keeps_newer_server_rows_unless_asked(client, session):
    data = _export(client)
    data["dashboard_widgets"][0]["order_index"] = 7
    data["dashboard_widgets"][0]["updated_at"] = 500  # older than the server's 1000
    data["workspace_chats"][0]["title"] = "Plan v2"
    data["workspace_chats"][0]["updated_at"] = "2026-02-01T00:00:00"  # newer
    resp = client.post("/api/internal/account/import/sam", headers=SECRET, json={"collections": data, "dry_run": False})
    out = resp.json()["collections"]
    assert out["dashboard_widgets"]["conflict"] == 1
    assert out["workspace_chats"]["update"] == 1
    session.expire_all()
    assert session.exec(select(UserWidget).where(UserWidget.username == "sam")).one().order_index == 1
    assert session.get(WorkspaceChat, "c1").title == "Plan v2"
    resp = client.post(
        "/api/internal/account/import/sam", headers=SECRET,
        json={"collections": data, "dry_run": False, "replace_conflicts": True},
    )
    session.expire_all()
    assert session.exec(select(UserWidget).where(UserWidget.username == "sam")).one().order_index == 7


def test_a_file_cannot_write_into_another_account(client, session):
    data = _export(client)
    # Someone edits the file to aim at pat's rows by id and owner.
    pat_widget = session.exec(select(UserWidget).where(UserWidget.username == "pat")).one()
    data["dashboard_widgets"] = [{**data["dashboard_widgets"][0], "id": pat_widget.id, "username": "pat", "widget_key": "x", "updated_at": 10**13}]
    data["workspace_chat_messages"] = [{"id": 1, "chat_id": "p1", "role": "user", "parts": "[]"}]
    data["profile"] = [{**data["profile"][0], "username": "pat", "display_name": "Hijacked", "family_role": "father"}]
    client.post("/api/internal/account/import/sam", headers=SECRET, json={"collections": data, "dry_run": False, "replace_conflicts": True})
    session.expire_all()
    pat = session.exec(select(User).where(User.username == "pat")).one()
    assert pat.display_name == "Pat"
    assert session.get(UserWidget, pat_widget.id).username == "pat"
    assert session.get(UserWidget, pat_widget.id).widget_key == "weather"
    sam = session.exec(select(User).where(User.username == "sam")).one()
    # The display name may be restored onto sam's own profile; the role never.
    assert sam.display_name == "Hijacked"
    assert sam.family_role == "other"
    msgs = session.exec(select(WorkspaceChatMessage).where(WorkspaceChatMessage.chat_id == "p1")).all()
    assert len(msgs) == 1


def test_restore_onto_a_new_server_remaps_child_rows(client, session):
    data = _export(client)
    # A new server: sam exists but has nothing yet, and the chat id is taken by someone else.
    for row in session.exec(select(WorkspaceChatMessage).where(WorkspaceChatMessage.chat_id == "c1")).all():
        session.delete(row)
    chat = session.get(WorkspaceChat, "c1")
    chat.username = "pat"
    session.add(chat)
    session.commit()
    out = client.post("/api/internal/account/import/sam", headers=SECRET, json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["workspace_chats"]["add"] == 1
    assert out["workspace_chat_messages"]["add"] == 1
    session.expire_all()
    mine = session.exec(select(WorkspaceChat).where(WorkspaceChat.username == "sam")).one()
    assert mine.id != "c1"
    assert session.exec(select(WorkspaceChatMessage).where(WorkspaceChatMessage.chat_id == mine.id)).one().role == "user"


def test_parent_rules_are_exported_but_never_restored(client, session):
    data = _export(client, "kid")
    assert data["screen_rules"]
    data["screen_rules"][0]["updated_at"] = "2999-01-01T00:00:00+00:00"
    out = client.post("/api/internal/account/import/kid", headers=SECRET, json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["screen_rules"]["skipped"] == 1
    assert out["screen_rules"]["update"] == 0


def test_may_manage(client):
    def ask(actor, subject):
        return client.get(f"/api/internal/account/may-manage?actor={actor}&subject={subject}", headers=SECRET).json()["allowed"]

    assert ask("sam", "sam")
    assert ask("admin", "kid")
    assert not ask("sam", "kid")
    assert not ask("nobody", "sam")


def test_audit_records_export(client, session):
    resp = client.post("/api/internal/account/audit", headers=SECRET, json={"action": "account.export", "actor": "admin", "target": "kid", "note": "for the child"})
    assert resp.status_code == 200
    event = session.exec(select(AuditEvent).where(AuditEvent.action == "account.export")).one()
    assert (event.actor, event.target) == ("admin", "kid")
    assert client.post("/api/internal/account/audit", headers=SECRET, json={"action": "user.delete"}).status_code == 422
