import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["WORKSPACE_DATABASE_URL"] = "sqlite:////tmp/test_ws_security.db"
os.environ["FERNET_KEY"] = "g13l5bpIeVaVe4ri66RE0bPYpB9IjCYdObQAKJU2Z14="

import httpx
import pytest

# Phase 4.2: Test Workspace Security
# This test attempts a path traversal attack and asserts a 403 Forbidden.

@pytest.mark.asyncio
async def test_workspace_path_traversal_blocked(monkeypatch):
    from sqlmodel import Session

    import services.workspace_runtime.main as wsrt
    from services.workspace_runtime.database import engine, init_db
    from services.workspace_runtime.main import app
    from services.workspace_runtime.models import Workspace

    init_db()
    with Session(engine) as session:
        existing = session.get(Workspace, "main")
        if existing:
            session.delete(existing)
            session.commit()
        ws = Workspace(
            id="main",
            display_name="Main Workspace",
            local_path="main",
            sync_mode="git",
            scope="user",
            capabilities=["read", "write"]
        )
        session.add(ws)
        session.commit()

    monkeypatch.setattr(wsrt, "_resolve_identity_context", lambda ref: {
        "user": ref.rag_user or "admin",
        "is_admin": True,
        "forbidden_branches": ["main", "master"],
    })

    from pathlib import Path
    tmp_root = Path("/tmp/test_ws_security_root")
    tmp_root.mkdir(exist_ok=True)
    monkeypatch.setattr(wsrt, "get_workspace_root", lambda: tmp_root)

    # We attempt to write to a path outside the workspace
    malicious_payload = {
        "workspace_id": "main",
        "relative_path": "../../../main.py",
        "content": "print('hacked')",
        "rag_user": "admin"
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "http://test/files/write",
            json=malicious_payload,
            headers={"X-Internal-Secret": "test-secret"}
        )

        # Should be blocked by resolve_safe_path with 403
        assert resp.status_code == 403
        detail = resp.json().get("detail", "")
        assert "Forbidden" in detail or "traversal" in detail.lower()
