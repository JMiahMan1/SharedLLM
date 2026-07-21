"""Unit tests for per-workspace secrets encrypted at rest (``env_enc``).

Covers the workspace_runtime merge of ``env`` / ``env_delete`` overrides,
Fernet encryption at rest, and the public masking of values (only key names
are exposed in list/get responses; the decrypted values are only returned by
the trusted internal ``/workspace/resolve`` endpoint).
"""
import os

# Must be set BEFORE importing the workspace_runtime app (it builds its engine
# and reads FERNET_KEY / INTERNAL_SECRET at import time).
os.environ.setdefault(
    "WORKSPACE_DATABASE_URL", "sqlite:////tmp/test_ws_env_enc_unit.db"
)
os.environ.setdefault(
    "FERNET_KEY", "g13l5bpIeVaVe4ri66RE0bPYpB9IjCYdObQAKJU2Z14="
)
os.environ.setdefault("INTERNAL_SECRET", "test-secret-ci")

import json

import pytest
from sqlmodel import Session

from services.workspace_runtime import main as wsrt
from services.workspace_runtime.crypto import decrypt, encrypt
from services.workspace_runtime.database import engine, init_db
from services.workspace_runtime.models import Workspace

pytestmark = pytest.mark.unit
SECRET = "test-secret-ci"


@pytest.fixture()
def ws_id():
    init_db()
    wid = f"unit_enc_{os.getpid()}_{id(object())}"
    with Session(engine) as s:
        s.add(Workspace(id=wid, display_name="Enc Test"))
        s.commit()
    yield wid
    with Session(engine) as s:
        obj = s.get(Workspace, wid)
        if obj:
            s.delete(obj)
            s.commit()


def _stored_env(wid: str) -> dict:
    with Session(engine) as s:
        ws = s.get(Workspace, wid)
        return json.loads(decrypt(ws.env_enc) or "{}")


def test_crypto_round_trip():
    ct = encrypt("hello")
    assert decrypt(ct) == "hello"
    assert encrypt(None) is None
    assert decrypt(None) is None


def test_update_workspace_encrypts_and_masks(ws_id):
    res = wsrt.update_workspace(
        ws_id,
        {"env": {"FOO": "bar", "GITHUB_TOKEN": "ws-specific"}},
        x_internal_secret=SECRET,
    )
    assert res["status"] == "SUCCESS"
    # Public response masks values: only key names, never values.
    assert res["workspace"]["env_keys"] == ["FOO", "GITHUB_TOKEN"]
    assert "env" not in res["workspace"]
    assert "env_enc" not in res["workspace"]
    # At rest it is encrypted and decrypts to the exact merged map.
    assert _stored_env(ws_id) == {"FOO": "bar", "GITHUB_TOKEN": "ws-specific"}


def test_update_workspace_merge_preserves_existing(ws_id):
    wsrt.update_workspace(ws_id, {"env": {"FOO": "bar"}}, x_internal_secret=SECRET)
    wsrt.update_workspace(ws_id, {"env": "BAZ"}, x_internal_secret=SECRET)
    wsrt.update_workspace(ws_id, {"env": {"BAZ": "qux"}}, x_internal_secret=SECRET)
    assert _stored_env(ws_id) == {"FOO": "bar", "BAZ": "qux"}


def test_update_workspace_env_delete(ws_id):
    wsrt.update_workspace(
        ws_id,
        {"env": {"FOO": "bar", "GITHUB_TOKEN": "tok", "BAZ": "qux"}},
        x_internal_secret=SECRET,
    )
    wsrt.update_workspace(ws_id, {"env_delete": ["FOO"]}, x_internal_secret=SECRET)
    assert _stored_env(ws_id) == {"GITHUB_TOKEN": "tok", "BAZ": "qux"}


def test_update_workspace_clear_key_via_none(ws_id):
    wsrt.update_workspace(
        ws_id, {"env": {"FOO": "bar", "BAZ": "qux"}}, x_internal_secret=SECRET
    )
    wsrt.update_workspace(ws_id, {"env": {"FOO": None}}, x_internal_secret=SECRET)
    assert _stored_env(ws_id) == {"BAZ": "qux"}


def test_workspace_to_dict_masks_values(ws_id):
    wsrt.update_workspace(
        ws_id, {"env": {"SECRET_KEY": "v"}}, x_internal_secret=SECRET
    )
    with Session(engine) as s:
        ws = s.get(Workspace, ws_id)
        d = wsrt._workspace_to_dict(ws)
    assert d["env_keys"] == ["SECRET_KEY"]
    assert "SECRET_KEY" not in d
    assert "env" not in d
    assert "env_enc" not in d


def test_update_workspace_ignores_readonly_fields(ws_id):
    # Simulate a frontend update passing the full serialised workspace representation
    # including 'created_at' and 'id'.
    res = wsrt.update_workspace(
        ws_id,
        {
            "id": ws_id,
            "created_at": "2026-07-13T16:16:49-07:00",
            "display_name": "Updated Enc Test"
        },
        x_internal_secret=SECRET,
    )
    assert res["status"] == "SUCCESS"
    assert res["workspace"]["display_name"] == "Updated Enc Test"
