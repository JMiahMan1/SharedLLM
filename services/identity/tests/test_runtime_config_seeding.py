"""Runtime settings that Identity seeds and the runtime config map resolves."""
import pytest
from sqlmodel import select

from services.identity.models import DEFAULT_GLOBAL_SETTINGS, GlobalSetting
from services.identity.seed import seed_from_env

_RUNTIME_DEFAULTS = {
    "workspace_root": "",
    "workspace_runtime_root": "/workspaces",
    "models_dir": "",
    "phrasebook_path": "",
    "announcement_blacklist": "",
    "gateway_internal_url": "",
    "artifact_local_quota_bytes": "5368709120",
}

_RUNTIME_ENV = {
    "WORKSPACE_ROOT": "/srv/workspaces",
    "WORKSPACE_RUNTIME_ROOT": "/srv/runtime-workspaces",
    "MODELS_DIR": "/srv/models",
    "PHRASEBOOK_PATH": "/srv/phrasebook.json",
    "ANNOUNCEMENT_BLACKLIST": "doorbell,garage",
    "GATEWAY_INTERNAL_URL": "http://gateway.internal:11435",
}


def _default_settings() -> dict[str, dict]:
    return {row["key"]: row for row in DEFAULT_GLOBAL_SETTINGS}


def _stored_settings(session) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(GlobalSetting)).all()}


def test_runtime_settings_have_safe_defaults():
    defaults = _default_settings()

    for key, value in _RUNTIME_DEFAULTS.items():
        assert key in defaults, f"{key} must be inserted by _ensure_default_settings"
        assert defaults[key]["value"] == value
        assert defaults[key].get("description"), f"{key} needs a description for the settings UI"

    # The two workspace roots are separate settings, not aliases: the
    # execution service and the workspace runtime each have their own mount.
    assert "workspace_runtime_root" in defaults["workspace_root"]["description"]
    assert "workspace_root" in defaults["workspace_runtime_root"]["description"]


def test_runtime_env_values_are_seeded(monkeypatch, identity_db_session):
    for key, value in _RUNTIME_ENV.items():
        monkeypatch.setenv(key, value)

    seed_from_env(identity_db_session)
    stored = _stored_settings(identity_db_session)

    expected = {
        "workspace_root": _RUNTIME_ENV["WORKSPACE_ROOT"],
        "workspace_runtime_root": _RUNTIME_ENV["WORKSPACE_RUNTIME_ROOT"],
        "models_dir": _RUNTIME_ENV["MODELS_DIR"],
        "phrasebook_path": _RUNTIME_ENV["PHRASEBOOK_PATH"],
        "announcement_blacklist": _RUNTIME_ENV["ANNOUNCEMENT_BLACKLIST"],
        "gateway_internal_url": _RUNTIME_ENV["GATEWAY_INTERNAL_URL"],
    }
    assert {key: stored[key] for key in expected} == expected


def test_runtime_env_seed_does_not_overwrite_operator_values(monkeypatch, identity_db_session):
    for key, value in _RUNTIME_ENV.items():
        monkeypatch.setenv(key, value)

    identity_db_session.add(
        GlobalSetting(key="workspace_runtime_root", value="/operator/workspaces")
    )
    identity_db_session.add(
        GlobalSetting(key="gateway_internal_url", value="http://operator-gateway:11435")
    )
    identity_db_session.commit()

    seed_from_env(identity_db_session)
    stored = _stored_settings(identity_db_session)

    assert stored["workspace_runtime_root"] == "/operator/workspaces"
    assert stored["gateway_internal_url"] == "http://operator-gateway:11435"
    assert stored["workspace_root"] == _RUNTIME_ENV["WORKSPACE_ROOT"]


@pytest.mark.asyncio
async def test_startup_inserts_missing_runtime_defaults(identity_db_session):
    from services.identity.main import _ensure_default_settings

    await _ensure_default_settings(identity_db_session)
    stored = _stored_settings(identity_db_session)

    assert {key: stored[key] for key in _RUNTIME_DEFAULTS} == _RUNTIME_DEFAULTS
