"""The service starts the way production starts it: no engine handed in.

Every other test injects its own engine, which hid a startup that read
app.state.engine before anything had created it (None), so the container
crash-looped on its first deploy.
"""
from fastapi.testclient import TestClient
from services.bible import main as bible_main


def test_startup_builds_its_own_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(bible_main, "BIBLE_DATABASE_URL", f"sqlite:///{tmp_path / 'bible.db'}")
    monkeypatch.setattr(bible_main.app.state, "engine", None)
    with TestClient(bible_main.app) as client:
        assert bible_main.app.state.engine is not None
        assert client.get("/health").status_code == 200
