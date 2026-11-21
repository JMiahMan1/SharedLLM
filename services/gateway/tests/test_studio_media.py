"""Generated art and narration must land in the workspace, with their provenance.

The backends already exist (the image proxy on the model host, the TTS engine in
execution); what is worth testing here is the contract around them: the bytes are
written where the game and the deck expect, the manifest remembers the prompt, and
a backend that returns nothing is an error rather than a blank picture.
"""
from __future__ import annotations

import base64
import json

import pytest

from services.gateway import workspace_api as ws
from services.gateway.studio import media


@pytest.fixture
def workspace(monkeypatch):
    """A fake workspace: records writes, replays a manifest."""
    state = {"files": {}, "bytes": []}

    async def fake_write(workspace_id, path, content, **kwargs):
        state["files"][path] = content
        return {"status": "SUCCESS"}

    async def fake_write_bytes(workspace_id, path, data, **kwargs):
        state["bytes"].append({"path": path, "data": data})
        state["files"][path] = f"<{len(data)} bytes>"
        return {"status": "SUCCESS"}

    async def fake_read(workspace_id, path, user="default", **kwargs):
        if path in state["files"]:
            return state["files"][path]
        raise ws.WorkspaceError("not found")

    monkeypatch.setattr(media.ws, "write_file", fake_write)
    monkeypatch.setattr(media.ws, "write_bytes", fake_write_bytes)
    monkeypatch.setattr(media.ws, "read_file", fake_read)
    return state


async def test_a_generated_picture_is_written_with_its_prompt_in_the_manifest(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout):
        assert url == "http://llm:11434"
        assert endpoint == "/v1/images/generations"
        assert payload["response_format"] == "b64_json"
        return {
            "model": "sdxl-turbo",
            "data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n").decode()}],
        }

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    asset = await media.generate_image("ws1", "a friendly blue robot", name="hero")

    assert asset.path == "assets/hero.png"
    assert asset.model == "sdxl-turbo"
    assert workspace["bytes"][0]["data"] == b"\x89PNG\r\n"
    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert manifest["assets"][0]["prompt"] == "a friendly blue robot"
    assert manifest["assets"][0]["path"] == "assets/hero.png"


async def test_re_generating_the_same_asset_replaces_its_manifest_entry(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout):
        return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    await media.generate_image("ws1", "first prompt", name="hero")
    await media.generate_image("ws1", "second prompt", name="hero")

    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert len(manifest["assets"]) == 1
    assert manifest["assets"][0]["prompt"] == "second prompt"


async def test_a_backend_that_returns_no_picture_is_an_error(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout):
        return {"status": "ERROR", "message": "CUDA out of memory"}

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    with pytest.raises(media.MediaError) as excinfo:
        await media.generate_image("ws1", "a castle")

    assert "CUDA out of memory" in str(excinfo.value)
    assert workspace["bytes"] == []


async def test_an_unconfigured_image_host_says_so(workspace, monkeypatch):
    async def no_base():
        raise RuntimeError("llm_local_url not configured")

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", no_base)

    with pytest.raises(media.MediaError) as excinfo:
        await media.generate_image("ws1", "a castle")

    assert "llm_local_url" in str(excinfo.value)


async def test_an_empty_prompt_is_refused_before_any_call(workspace):
    with pytest.raises(media.MediaError):
        await media.generate_image("ws1", "   ")


async def test_narration_is_asked_of_tts_and_recorded(workspace, monkeypatch):
    async def fake_post(url, endpoint, payload, timeout):
        assert endpoint == "/execute/tts"
        assert payload["workspace_id"] == "ws1"
        assert payload["relative_path"] == "assets/title.wav"
        return {"status": "SUCCESS", "detail": {"relative_path": "assets/title.wav", "voice": "af_heart"}}

    monkeypatch.setattr(media, "_post", fake_post)

    asset = await media.narrate("ws1", "Welcome to the series.", name="title")

    assert asset.kind == "audio" and asset.path == "assets/title.wav"
    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert manifest["assets"][0]["kind"] == "audio"


async def test_a_silent_manifest_reads_as_empty(workspace):
    payload = await media.manifest("ws1")

    assert payload == {"assets": [], "updated_at": None}


def test_asset_names_are_file_safe():
    assert media.asset_path("Star Catcher: Hero!") == "assets/star-catcher-hero.png"
    assert media.asset_path("title", suffix=".wav") == "assets/title.wav"
