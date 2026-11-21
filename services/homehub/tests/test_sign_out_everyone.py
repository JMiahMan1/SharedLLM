"""Identity's sign-in reset reaches the hub: every person's hub login is revoked.

Companion-app logins and people's long-lived tokens stop working; a service's own
long-lived token (Music Assistant and the like) is kept and named; system tokens are
never touched. Only an admin or the stack may ask. No network, made-up values only.
"""
from __future__ import annotations

import pytest

from services.homehub import ha_auth
from services.homehub.auth_providers.jarvis_identity import provider_user
from services.homehub.tests.test_ha_auth import ANDROID, APP_TOKEN, SECRET, env  # noqa: F401  (fixture)


def test_sign_out_everyone_revokes_people_and_keeps_services(env):  # noqa: F811
    c, auth, runtime = env["client"], env["auth"], env["runtime"]
    ha = runtime.ha_auth
    service_user = ha.ensure_service_user("Music Assistant")
    service_token = ha.create_long_lived_token(service_user, "Music Assistant")
    access = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN,
                                         "client_id": ANDROID}).json()["access_token"]
    assert auth.authenticate(access) is not None

    resp = c.post("/hub/auth/sign-out-everyone", headers={"X-Internal-Secret": SECRET})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["companion_logins_revoked"] == 2  # the live app login and the expired one
    assert body["people_long_lived_revoked"] == 1
    assert body["service_tokens_kept"] == ["Music Assistant"]

    assert auth.authenticate(access) is None
    again = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN,
                                        "client_id": ANDROID})
    assert again.json()["error"] == "invalid_grant"
    assert auth.authenticate(service_token) is not None
    tokens = env["store"].load_blob(ha_auth.BLOB)["tokens"]
    assert "rt-sys" in tokens  # system tokens are never touched


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Secret": "wrong"}])
def test_only_an_admin_or_the_stack_may_sign_everyone_out(env, headers):  # noqa: F811
    assert env["client"].post("/hub/auth/sign-out-everyone", headers=headers).status_code == 401


def test_a_test_account_is_never_a_hub_user():
    row = {"id": 7, "username": "test-agent", "display_name": "Test agent", "is_test_account": True}
    assert provider_user(row) is None
    assert provider_user({**row, "is_test_account": False}) is not None
