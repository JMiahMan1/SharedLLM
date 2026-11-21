"""Sign everyone out and reset keys, and the automated agents' test account.

All values here are made up. Keys are compared by behaviour (the old one is refused,
the new one works); a key's value is never printed by the code under test.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import sign_in_reset
from services.identity.main import (
    _store_generated_api_key,
    _store_user_api_key,
    app,
    get_session,
    require_internal,
    sign_in_reset_router,
)
from services.identity.models import APIKey, AuditEvent, Device, GlobalSetting, RevokedCredential, User
from services.identity.seed import hash_password

ADMIN_KEY = "fake-admin-key-0001"
KID_KEY = "fake-kid-key-0002"
SATELLITE_KEY = "fake-satellite-key-0003"
CLIENT_KEY = "fake-client-key-0004"
AGENT_KEY = "wg_fake-agent-key-0005"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        admin = User(username="parent", is_admin=True, family_role="father", password_hash=hash_password("pw-one"))
        kid = User(username="kid", family_role="child", password_hash=hash_password("pw-two"))
        system = User(username="default", is_admin=True, is_system_default=True)
        for u in (admin, kid, system):
            session.add(u)
        session.commit()
        _store_user_api_key(admin, ADMIN_KEY)
        _store_user_api_key(kid, KID_KEY)
        _store_user_api_key(system, "fake-system-key-0006")
        sat = APIKey(user_id=admin.id, label="device:Kitchen satellite")
        _store_generated_api_key(sat, SATELLITE_KEY)
        other = APIKey(user_id=kid.id, label="Homework script")
        _store_generated_api_key(other, CLIENT_KEY)
        pc = Device(device_key="windows-fake-guid", kind="windows-pc", label="Study PC", owner_username="kid",
                    registered_by="admin")
        from services.identity.crypto import digest_secret

        pc.agent_key_hash = digest_secret(AGENT_KEY) or ""
        admin.github_token_enc = "gAAAAA-not-a-real-token"
        for row in (admin, kid, system, sat, other, pc):
            session.add(row)
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True
    calls: list[str] = []

    def fake_hub() -> dict:
        calls.append("hub")
        return {"status": "revoked", "revoked": 3, "message": "The hub signed out 3 login(s).",
                "service_tokens_kept": ["Music Assistant"]}

    sign_in_reset_router.hub_sign_out = fake_hub
    with TestClient(app) as client:
        client.hub_calls = calls  # type: ignore[attr-defined]
        yield client
    sign_in_reset_router.hub_sign_out = None
    app.dependency_overrides.clear()


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _me(client, key: str):
    return client.get("/api/users/me", headers=_auth(key))


def test_preview_lists_what_needs_re_enrolment_without_secrets(client):
    resp = client.get("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY))
    assert resp.status_code == 200
    body = resp.json()
    assert body["users"] == 3
    assert body["api_keys"] == 2
    assert body["device_agent_keys"] == 1
    kinds = {i["kind"] for i in body["re_enrol"]}
    assert {"windows_guard", "esphome_satellite", "api_key", "homehub_companion_app"} <= kinds
    assert any(t["field"] == "github_token" and t["accounts"] == 1 for t in body["third_party"])
    text = resp.text
    for secret in (ADMIN_KEY, KID_KEY, SATELLITE_KEY, CLIENT_KEY, AGENT_KEY, "gAAAAA"):
        assert secret not in text


def test_only_an_admin_may_reset(client):
    assert client.get("/api/users/security/sign-in-reset", headers=_auth(KID_KEY)).status_code == 403
    resp = client.post("/api/users/security/sign-in-reset", headers=_auth(KID_KEY), json={"confirm": "RESET"})
    assert resp.status_code == 403
    assert _me(client, KID_KEY).status_code == 200


def test_reset_needs_the_confirm_word(client):
    resp = client.post("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY), json={})
    assert resp.status_code == 422
    assert _me(client, ADMIN_KEY).status_code == 200


def test_reset_everyone_revokes_every_key_and_says_why(client, session):
    resp = client.post("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY), json={"confirm": "RESET"})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["scope"] == "everyone"
    assert report["users_reset"] == 3
    assert report["api_keys_revoked"] == 2
    assert report["device_agent_keys_revoked"] == 1
    assert report["caller_signed_out"] is True
    assert report["homehub"]["status"] == "revoked"
    assert client.hub_calls == ["hub"]
    assert any(i["kind"] == "homehub_long_lived_token" for i in report["re_enrol"])
    for secret in (ADMIN_KEY, KID_KEY, SATELLITE_KEY, CLIENT_KEY, AGENT_KEY):
        assert secret not in resp.text

    for old in (ADMIN_KEY, KID_KEY, SATELLITE_KEY, CLIENT_KEY):
        refused = _me(client, old)
        assert refused.status_code == 401
        assert refused.json() == {"detail": sign_in_reset.SIGN_IN_RESET_MESSAGE, "code": "sign_in_reset"}

    gate = client.get("/api/internal/validate-api-key", headers={"X-Api-Key": KID_KEY})
    assert gate.status_code == 401
    assert gate.json()["code"] == "sign_in_reset"

    # The PC agent key is gone too.
    pc = session.exec(select(Device).where(Device.device_key == "windows-fake-guid")).one()
    assert pc.agent_key_hash == ""
    assert session.exec(select(APIKey)).all() == []
    assert len(session.exec(select(RevokedCredential)).all()) == 3 + 2 + 1

    # A password sign-in hands out the new key, which works.
    login = client.post("/api/auth/login", json={"username": "kid", "password": "pw-two"})
    assert login.status_code == 200
    new_key = login.json()["api_key"]
    assert new_key and new_key != KID_KEY
    assert _me(client, new_key).status_code == 200

    # The report is kept for the admin, and the action is audited (no values).
    last = client.get("/api/users/security/sign-in-reset/last",
                      headers=_auth(client.post("/api/auth/login", json={"username": "parent", "password": "pw-one"}).json()["api_key"]))
    assert last.status_code == 200
    assert last.json()["users_reset"] == 3
    events = session.exec(select(AuditEvent).where(AuditEvent.action == "security.sign_in_reset")).all()
    assert len(events) == 1


def test_an_unknown_key_is_refused_with_a_stable_code(client):
    resp = _me(client, "fake-never-issued-key")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid API Key", "code": "api_key_invalid"}
    gate = client.get("/api/internal/validate-api-key", headers={"X-Api-Key": "fake-never-issued-key"})
    assert gate.json()["code"] == "api_key_invalid"


def test_no_key_is_a_plain_401(client):
    resp = client.get("/api/users/me")
    assert resp.status_code == 401
    assert "code" not in resp.json()


def test_reset_one_person_leaves_everyone_else_signed_in(client, session):
    resp = client.post("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY),
                       json={"confirm": "RESET", "username": "kid"})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["scope"] == "user"
    assert report["username"] == "kid"
    assert report["caller_signed_out"] is False
    assert report["homehub"]["status"] == "skipped"
    assert client.hub_calls == []
    assert _me(client, KID_KEY).json().get("code") == "sign_in_reset"
    assert _me(client, ADMIN_KEY).status_code == 200
    # The admin's satellite key is not the kid's: it still works.
    assert _me(client, SATELLITE_KEY).status_code == 200
    labels = {i["label"] for i in report["re_enrol"]}
    assert "Study PC" in labels and "Homework script" in labels and "Kitchen satellite" not in labels


def test_reset_of_an_unknown_person_is_404(client):
    resp = client.post("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY),
                       json={"confirm": "RESET", "username": "nobody"})
    assert resp.status_code == 404


def test_hub_is_skipped_with_a_message_when_not_configured(monkeypatch):
    monkeypatch.setattr(sign_in_reset.config, "HOMEHUB_URL", "")
    result = sign_in_reset.hub_sign_out_from_env()
    assert result["status"] == "skipped"
    assert "HOMEHUB_URL" in result["message"]


def test_hub_answer_is_summarised(monkeypatch):
    monkeypatch.setattr(sign_in_reset.config, "HOMEHUB_URL", "http://hub.invalid:8123")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"companion_logins_revoked": 2, "people_long_lived_revoked": 1, "service_tokens_kept": ["MA"]}'

    def fake_open(req, timeout):
        seen["url"] = req.full_url
        seen["has_secret_header"] = bool(req.get_header("X-internal-secret"))
        return _Resp()

    monkeypatch.setattr(sign_in_reset.urllib.request, "urlopen", fake_open)
    result = sign_in_reset.hub_sign_out_from_env()
    assert result["status"] == "revoked" and result["revoked"] == 3
    assert result["service_tokens_kept"] == ["MA"]
    assert seen["url"] == "http://hub.invalid:8123/hub/auth/sign-out-everyone"
    assert seen["has_secret_header"] is True


def test_hub_unreachable_is_reported_as_failed(monkeypatch):
    monkeypatch.setattr(sign_in_reset.config, "HOMEHUB_URL", "http://127.0.0.1:9")

    def refuse(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(sign_in_reset.urllib.request, "urlopen", refuse)
    result = sign_in_reset.hub_sign_out_from_env()
    assert result["status"] == "failed"
    assert "reset_sign_ins.sh" in result["message"]


# --- the test account -------------------------------------------------------------

def test_test_account_is_created_least_privileged_and_works(client, session):
    resp = client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "test-agent" and body["created"] is True
    key = body["api_key"]
    me = _me(client, key)
    assert me.status_code == 200
    data = me.json()
    assert data["is_test_account"] is True
    assert data["is_admin"] is False
    assert data["permissions"] == []
    # No admin route.
    assert client.get("/api/users/security/sign-in-reset", headers=_auth(key)).status_code == 403
    # No password sign-in.
    assert client.post("/api/auth/login", json={"username": "test-agent", "password": "anything"}).status_code == 401


def test_test_account_is_out_of_the_roster(client, session):
    client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={})
    roster = client.get("/api/users", headers=_auth(ADMIN_KEY)).json()
    assert "test-agent" not in {u["username"] for u in roster}
    everyone = client.get("/api/users?include_test_accounts=true", headers=_auth(ADMIN_KEY)).json()
    marked = [u for u in everyone if u["username"] == "test-agent"]
    assert marked and marked[0]["is_test_account"] is True
    household = client.get("/api/internal/household").json()
    assert "test-agent" not in household["usernames"]
    directory = client.get("/api/users/family-directory", headers=_auth(KID_KEY)).json()
    assert "test-agent" not in {u["username"] for u in directory}
    recipients = client.get("/api/users/sharing-recipients", headers=_auth(KID_KEY)).json()
    assert "test-agent" not in {u["username"] for u in recipients}


def test_test_account_cannot_be_promoted(client, session):
    client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={})
    resp = client.patch("/api/users/test-agent", headers=_auth(ADMIN_KEY), json={"is_admin": True})
    assert resp.status_code == 422
    user = session.exec(select(User).where(User.username == "test-agent")).one()
    session.refresh(user)
    assert user.is_admin is False


def test_rekeying_the_test_account_revokes_its_old_key(client):
    first = client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={}).json()["api_key"]
    again = client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={})
    assert again.json()["created"] is False
    second = again.json()["api_key"]
    assert second != first
    assert _me(client, first).json().get("code") == "sign_in_reset"
    assert _me(client, second).status_code == 200


def test_a_household_account_is_never_turned_into_a_test_account(client):
    resp = client.post("/api/users/security/test-account", headers=_auth(ADMIN_KEY), json={"username": "kid"})
    assert resp.status_code == 409
    assert _me(client, KID_KEY).status_code == 200


def test_the_report_setting_holds_no_secret(client, session):
    client.post("/api/users/security/sign-in-reset", headers=_auth(ADMIN_KEY), json={"confirm": "RESET"})
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == sign_in_reset.LAST_RESET_SETTING)).one()
    for secret in (ADMIN_KEY, KID_KEY, SATELLITE_KEY, CLIENT_KEY, AGENT_KEY):
        assert secret not in row.value
