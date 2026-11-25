"""services/gateway/voice_hold_music_routes.py: the hold music setting and the clip upload, passed to HomeHub."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.gateway import automation_routes, voice_hold_music_routes


@pytest.fixture
def client():
    from services.gateway.main import app

    return TestClient(app)


@pytest.fixture
def admin(monkeypatch):
    async def _always_admin(request):
        return "admin"

    monkeypatch.setattr(automation_routes, "_admin", _always_admin)


@pytest.fixture
def hub(monkeypatch):
    seen = []

    async def call(method, path, *, creds=None, params=None, json=None, timeout=None):
        seen.append((method, path, json))
        if json and json.get("source") == "jazz":
            return 400, {"message": "source must be one of gameshow, builtin, upload"}
        return 200, {"source": (json or {}).get("source", "gameshow")}

    monkeypatch.setattr(automation_routes, "_hub", call)
    return seen


@pytest.fixture
def uploads(monkeypatch):
    sent = []

    async def send(raw, filename):
        sent.append((raw, filename))
        if filename.endswith(".txt"):
            return 415, {"detail": "Use a wav, mp3, ogg or m4a file"}
        return 200, {"source": "upload", "upload": {"name": filename}}

    monkeypatch.setattr(voice_hold_music_routes, "_send_clip", send)
    return sent


def test_only_admins_reach_it(client, monkeypatch):
    from fastapi import HTTPException

    async def refuse(request):
        raise HTTPException(status_code=403, detail="Admins only")

    monkeypatch.setattr(automation_routes, "_admin", refuse)
    assert client.get("/api/homehub/voice/hold-music").status_code == 403
    assert client.put("/api/homehub/voice/hold-music", json={"source": "builtin"}).status_code == 403
    assert client.post("/api/homehub/voice/hold-music/clip", files={"file": ("a.wav", b"RIFF")}).status_code == 403
    assert client.delete("/api/homehub/voice/hold-music/clip").status_code == 403


def test_the_setting_is_read_and_changed_through_the_hub(client, admin, hub):
    assert client.get("/api/homehub/voice/hold-music").json() == {"source": "gameshow"}
    put = client.put("/api/homehub/voice/hold-music", json={"source": "builtin", "lines": ["Hi."], "extra": 1})
    assert put.json() == {"source": "builtin"}
    assert hub[-1] == ("PUT", "/hub/voice/hold-music", {"source": "builtin", "lines": ["Hi."]})  # nothing else
    bad = client.put("/api/homehub/voice/hold-music", json={"source": "jazz"})
    assert bad.status_code == 400 and "source must be one of" in bad.json()["detail"]
    assert client.put("/api/homehub/voice/hold-music", json=[1]).status_code == 400
    assert client.delete("/api/homehub/voice/hold-music/clip").status_code == 200
    assert hub[-1][:2] == ("DELETE", "/hub/voice/hold-music/clip")


def test_a_clip_is_sent_to_the_hub_as_it_came(client, admin, uploads):
    ok = client.post("/api/homehub/voice/hold-music/clip", files={"file": ("theme.mp3", b"ID3data", "audio/mpeg")})
    assert ok.status_code == 200 and ok.json()["upload"]["name"] == "theme.mp3"
    assert uploads == [(b"ID3data", "theme.mp3")]
    refused = client.post("/api/homehub/voice/hold-music/clip", files={"file": ("notes.txt", b"hello")})
    assert refused.status_code == 415 and "Use a wav" in refused.json()["detail"]


def test_a_missing_empty_or_too_big_clip_never_reaches_the_hub(client, admin, uploads):
    assert client.post("/api/homehub/voice/hold-music/clip", files={"other": ("a.wav", b"x")}).status_code == 422
    assert client.post("/api/homehub/voice/hold-music/clip", files={"file": ("a.wav", b"")}).status_code == 400
    big = b"x" * (voice_hold_music_routes.MAX_UPLOAD_BYTES + 1)
    resp = client.post("/api/homehub/voice/hold-music/clip", files={"file": ("a.wav", big)})
    assert resp.status_code == 413 and "5 MB" in resp.json()["detail"]
    assert uploads == []
