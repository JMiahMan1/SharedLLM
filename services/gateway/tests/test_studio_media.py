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
    """A workspace that keeps files and honours expected_sha256 like the runtime."""
    state = {"files": {}, "bytes": []}

    def _check(path, expected_sha256):
        if expected_sha256 is None:
            return
        current = state["files"].get(path)
        if expected_sha256 == ws.CREATE_ONLY:
            if current is not None:
                raise ws.WorkspaceConflict(f"{path} exists", 409)
        elif current is None or ws.sha256(current) != expected_sha256:
            raise ws.WorkspaceConflict(f"{path} changed", 409)

    async def fake_write(workspace_id, path, content, *, expected_sha256=None, user, create_parents=True):
        assert user == "u1"
        _check(path, expected_sha256)
        state["files"][path] = content
        return {"status": "SUCCESS"}

    async def fake_write_bytes(workspace_id, path, data, *, expected_sha256=None, user, create_parents=True):
        state["bytes"].append({"path": path, "data": data})
        state["files"][path] = f"<{len(data)} bytes>"
        return {"status": "SUCCESS"}

    async def fake_read_exact(workspace_id, path, *, user, max_bytes=ws.MAX_FILE_BYTES):
        if path in state["files"]:
            return state["files"][path]
        raise ws.WorkspaceFileNotFound(f"Path not found: {path}", 404)

    monkeypatch.setattr(media.ws, "write_file", fake_write)
    monkeypatch.setattr(media.ws, "write_bytes", fake_write_bytes)
    monkeypatch.setattr(media.ws, "read_text_exact", fake_read_exact)
    return state


async def test_a_generated_picture_is_written_with_its_prompt_in_the_manifest(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout, *, internal):
        assert url == "http://llm:11434"
        assert endpoint == "/v1/images/generations"
        assert payload["response_format"] == "b64_json"
        assert internal is False, "the image host never gets the internal secret"
        return {
            "model": "sdxl-turbo",
            "data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n").decode()}],
        }

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    asset = await media.generate_image("ws1", "a friendly blue robot", name="hero", user="u1")

    assert asset.path == "assets/hero.png"
    assert asset.model == "sdxl-turbo"
    assert workspace["bytes"][0]["data"] == b"\x89PNG\r\n"
    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert manifest["assets"][0]["prompt"] == "a friendly blue robot"
    assert manifest["assets"][0]["path"] == "assets/hero.png"


async def test_re_generating_the_same_asset_replaces_its_manifest_entry(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout, *, internal):
        return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    await media.generate_image("ws1", "first prompt", name="hero", user="u1")
    await media.generate_image("ws1", "second prompt", name="hero", user="u1")

    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert len(manifest["assets"]) == 1
    assert manifest["assets"][0]["prompt"] == "second prompt"


async def test_a_backend_that_returns_no_picture_is_an_error(workspace, monkeypatch):
    async def fake_base():
        return "http://llm:11434"

    async def fake_post(url, endpoint, payload, timeout, *, internal):
        return {"status": "ERROR", "message": "CUDA out of memory"}

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", fake_base)
    monkeypatch.setattr(media, "_post", fake_post)

    with pytest.raises(media.MediaError) as excinfo:
        await media.generate_image("ws1", "a castle", user="u1")

    assert "CUDA out of memory" in str(excinfo.value)
    assert workspace["bytes"] == []


async def test_an_unconfigured_image_host_says_so(workspace, monkeypatch):
    async def no_base():
        raise RuntimeError("llm_local_url not configured")

    monkeypatch.setattr("services.gateway.agent_loop.sd_proxy_base_url", no_base)

    with pytest.raises(media.MediaError) as excinfo:
        await media.generate_image("ws1", "a castle", user="u1")

    assert "llm_local_url" in str(excinfo.value)


async def test_an_empty_prompt_is_refused_before_any_call(workspace):
    with pytest.raises(media.MediaError):
        await media.generate_image("ws1", "   ", user="u1")


async def test_narration_is_asked_of_tts_and_recorded(workspace, monkeypatch):
    async def fake_post(url, endpoint, payload, timeout, *, internal):
        assert endpoint == "/execute/tts"
        assert payload["workspace_id"] == "ws1"
        assert payload["relative_path"] == "assets/title.wav"
        return {"status": "SUCCESS", "detail": {"relative_path": "assets/title.wav", "voice": "af_heart"}}

    monkeypatch.setattr(media, "_post", fake_post)

    asset = await media.narrate("ws1", "Welcome to the series.", name="title", user="u1")

    assert asset.kind == "audio" and asset.path == "assets/title.wav"
    manifest = json.loads(workspace["files"][media.MANIFEST_PATH])
    assert manifest["assets"][0]["kind"] == "audio"


async def test_a_missing_manifest_reads_as_empty(workspace):
    payload = await media.manifest("ws1", user="u1")

    assert payload == {"assets": [], "updated_at": None}


async def test_a_manifest_that_cannot_be_read_is_an_error_not_an_empty_list(workspace, monkeypatch):
    async def unreachable(workspace_id, path, *, user, max_bytes=ws.MAX_FILE_BYTES):
        raise ws.WorkspaceError("Could not read assets/manifest.json: 503", 503)

    monkeypatch.setattr(media.ws, "read_text_exact", unreachable)

    with pytest.raises(ws.WorkspaceError):
        await media.manifest("ws1", user="u1")


async def test_a_corrupt_manifest_is_reported_and_never_overwritten(workspace, monkeypatch):
    workspace["files"][media.MANIFEST_PATH] = "{half a manifest"

    async def fake_post(url, endpoint, payload, timeout, *, internal):
        return {"status": "SUCCESS", "detail": {"relative_path": "assets/a.wav"}}

    monkeypatch.setattr(media, "_post", fake_post)

    with pytest.raises(media.MediaError, match="not valid JSON"):
        await media.narrate("ws1", "hello", name="a", user="u1")
    assert workspace["files"][media.MANIFEST_PATH] == "{half a manifest"


async def test_a_manifest_changed_by_another_writer_is_merged_not_lost(workspace, monkeypatch):
    workspace["files"][media.MANIFEST_PATH] = json.dumps({"assets": [{"path": "assets/old.png"}]})
    real_write = media.ws.write_file
    raced = {"done": False}

    async def racing_write(workspace_id, path, content, *, expected_sha256=None, user, create_parents=True):
        if not raced["done"]:
            raced["done"] = True  # another generation lands between our read and write
            workspace["files"][path] = json.dumps(
                {"assets": [{"path": "assets/old.png"}, {"path": "assets/other.png"}]}
            )
        return await real_write(workspace_id, path, content, expected_sha256=expected_sha256, user=user)

    async def fake_post(url, endpoint, payload, timeout, *, internal):
        return {"status": "SUCCESS", "detail": {"relative_path": "assets/new.wav"}}

    monkeypatch.setattr(media.ws, "write_file", racing_write)
    monkeypatch.setattr(media, "_post", fake_post)

    await media.narrate("ws1", "hello", name="new", user="u1")

    paths = [item["path"] for item in json.loads(workspace["files"][media.MANIFEST_PATH])["assets"]]
    assert paths == ["assets/old.png", "assets/other.png", "assets/new.wav"]


def test_asset_names_are_file_safe():
    assert media.asset_path("Star Catcher: Hero!") == "assets/star-catcher-hero.png"
    assert media.asset_path("title", suffix=".wav") == "assets/title.wav"
