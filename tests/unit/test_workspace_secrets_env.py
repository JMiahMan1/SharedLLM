"""Unit tests for per-workspace secret/env injection into the sandbox.

Covers the execution-layer merge of Identity integration secrets (defaults)
with a workspace's own per-workspace env/secret overrides. The workspace layer
wins; a ``None`` override clears an inherited default. This is the logic used
to build the environment for every command run inside a workspace sandbox
(``services.execution.handlers.workspace.build_sandbox_env``).
"""
import pytest

from services.execution.handlers.workspace import (
    _identity_secrets_to_env,
    build_sandbox_env,
)

pytestmark = pytest.mark.unit


def test_identity_secrets_map_github_token():
    env = _identity_secrets_to_env({"github_token": "gh-tok"})
    assert env["GITHUB_TOKEN"] == "gh-tok"
    assert env["GH_TOKEN"] == "gh-tok"
    assert env["GH_ENTERPRISE_TOKEN"] == "gh-tok"


def test_identity_secrets_maps_all_known_integrations():
    uc = {
        "github_token": "gh",
        "git_token": "gt",
        "gitlab_token": "gl",
        "nextcloud_pass": "ncp",
        "nextcloud_url": "ncu",
        "ha_token": "hat",
        "ha_url": "hau",
        "api_key": "ak",
    }
    env = _identity_secrets_to_env(uc)
    assert env["GITHUB_TOKEN"] == "gh"
    assert env["GIT_TOKEN"] == "gt"
    assert env["GITLAB_TOKEN"] == "gl"
    assert env["NEXTCLOUD_PASSWORD"] == "ncp"
    assert env["NEXTCLOUD_PASS"] == "ncp"
    assert env["NEXTCLOUD_URL"] == "ncu"
    assert env["HA_TOKEN"] == "hat"
    assert env["HOME_ASSISTANT_TOKEN"] == "hat"
    assert env["HA_URL"] == "hau"
    assert env["API_KEY"] == "ak"


def test_identity_secrets_skips_empty_and_unknown():
    env = _identity_secrets_to_env(
        {"github_token": "", "git_token": None, "unknown": "x"}
    )
    assert "GITHUB_TOKEN" not in env
    assert "GIT_TOKEN" not in env
    assert "unknown" not in env


def test_identity_secrets_none_user_ctx():
    assert _identity_secrets_to_env(None) == {}
    assert _identity_secrets_to_env("not a dict") == {}


def test_build_sandbox_env_injects_identity_defaults():
    env = build_sandbox_env({"github_token": "gh-default"}, None)
    assert env["GITHUB_TOKEN"] == "gh-default"


def test_build_sandbox_env_workspace_override_wins():
    env = build_sandbox_env(
        {"github_token": "gh-default"},
        {"GITHUB_TOKEN": "gh-workspace"},
    )
    assert env["GITHUB_TOKEN"] == "gh-workspace"


def test_build_sandbox_env_none_override_clears_inherited_default():
    env = build_sandbox_env(
        {"github_token": "gh-default"},
        {"GITHUB_TOKEN": None},
    )
    assert "GITHUB_TOKEN" not in env


def test_build_sandbox_env_adds_new_key():
    env = build_sandbox_env({"github_token": "gh"}, {"CUSTOM_VAR": "v"})
    assert env["CUSTOM_VAR"] == "v"
    assert env["GITHUB_TOKEN"] == "gh"


def test_build_sandbox_env_non_dict_override_ignored():
    env = build_sandbox_env({"github_token": "gh"}, "not-a-dict")
    # Non-dict override is ignored; the Identity-derived env is returned intact.
    assert env["GITHUB_TOKEN"] == "gh"
    assert env["GH_TOKEN"] == "gh"
    assert env["GH_ENTERPRISE_TOKEN"] == "gh"
    assert set(env.keys()) == {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GH_ENTERPRISE_TOKEN",
    }


def test_build_sandbox_env_values_coerced_to_str():
    env = build_sandbox_env({}, {"N": 123})
    assert env["N"] == "123"
