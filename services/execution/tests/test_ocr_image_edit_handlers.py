"""Unit tests for the workspace-scoped OCR and image edit handlers."""
import base64
from pathlib import Path

import pytest

from services.execution.handlers import image_edit as image_edit_mod
from services.execution.handlers import ocr as ocr_mod
from services.execution.schemas import ImageEditRequest, OcrRequest


def _async(value):
    async def _inner():
        return value

    return _inner


def _make_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 48), color=(200, 40, 40)).save(path)


class _FakeResponse:
    def __init__(self, status, json_data=None, text_data=""):
        self.status = status
        self._json = json_data
        self._text = text_data

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        return self._handler(url, kwargs)


def _patch_aiohttp(monkeypatch, handler):
    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeClient(handler))


def _ocr_req(**overrides):
    fields = {
        "user_context": {"user": "default", "is_admin": True},
        "workspace_id": "Test",
        "image_path": "sign_original.jpg",
    }
    fields.update(overrides)
    return OcrRequest(**fields)


def _edit_req(**overrides):
    fields = {
        "user_context": {"user": "default", "is_admin": True},
        "workspace_id": "Test",
        "image_path": "sign_original.jpg",
        "prompt": "make it look old",
    }
    fields.update(overrides)
    return ImageEditRequest(**fields)


@pytest.mark.asyncio
async def test_ocr_success_returns_extracted_text(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(ocr_mod, "_resolve_workspace_info", fake_resolve)

    async def fake_ocr(path, user_context=None, proxy_url=None, model=None, task="general"):
        return {"full_text": "MESANAZ.ORG", "headline": "Sunday Worship", "subtext": "", "badge": ""}

    monkeypatch.setattr(ocr_mod.vision_ocr, "vision_ocr_screenshot", fake_ocr)

    result = await ocr_mod.handle_ocr(_ocr_req())
    assert result.status == "SUCCESS"
    assert "MESANAZ.ORG" in result.detail["full_text"]
    assert result.detail["headline"] == "Sunday Worship"


@pytest.mark.asyncio
async def test_ocr_missing_image_fails(tmpdir, monkeypatch):
    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(ocr_mod, "_resolve_workspace_info", fake_resolve)

    result = await ocr_mod.handle_ocr(_ocr_req())
    assert result.status == "FAILURE"
    assert "not found" in result.message


@pytest.mark.asyncio
async def test_ocr_without_workspace_fails(tmpdir, monkeypatch):
    async def raise_missing(ws, uc):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="No workspace_id provided.")

    monkeypatch.setattr(ocr_mod, "_resolve_workspace_info", raise_missing)

    result = await ocr_mod.handle_ocr(_ocr_req(workspace_id=None))
    assert result.status == "FAILURE"
    assert "workspace_id" in result.message.lower()


@pytest.mark.asyncio
async def test_image_edit_missing_model_fails_loudly(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async(None))

    result = await image_edit_mod.handle_image_edit(_edit_req())
    assert result.status == "FAILURE"
    assert "image_edit_model" in result.message
    assert "Settings" in result.message


@pytest.mark.asyncio
async def test_image_edit_success_saves_to_workspace(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))

    b64 = base64.b64encode(b"fake-edited-png-bytes").decode()

    def route(url, kwargs):
        if "images/edits" in url:
            return _FakeResponse(200, json_data={"data": [{"b64_json": b64}]})
        if "files/write" in url:
            return _FakeResponse(200, json_data={"status": "SUCCESS"})
        return _FakeResponse(500, text_data=f"unexpected url: {url}")

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(_edit_req(proxy_url="http://proxy:11434"))

    assert result.status == "SUCCESS"
    assert result.detail["output_path"] == "sign_original_edited.jpg"
    assert result.detail["model"] == "qwen-image-edit-rapid-aio:q4_k"


@pytest.mark.asyncio
async def test_image_edit_api_error_fails(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))

    def route(url, kwargs):
        return _FakeResponse(400, json_data={"detail": "model not loaded"})

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(_edit_req(proxy_url="http://proxy:11434"))

    assert result.status == "FAILURE"
    assert "API error" in result.message


@pytest.mark.asyncio
async def test_image_edit_invalid_size_fails(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))

    result = await image_edit_mod.handle_image_edit(
        _edit_req(proxy_url="http://proxy:11434", size="99999x99999")
    )
    assert result.status == "FAILURE"
    assert "out of range" in result.message


@pytest.mark.asyncio
async def test_image_edit_save_sends_plain_dict_user_context(tmpdir, monkeypatch):
    """Regression: pydantic UserContext leaked into the aiohttp json= payload and
    blew up with 'Object of type UserContext is not JSON serializable' — the
    workspace save must receive a plain dict that json.dumps can encode."""
    import json

    _make_png(Path(tmpdir) / "sign_original.jpg")

    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))

    b64 = base64.b64encode(b"fake-edited-png-bytes").decode()
    captured = {}

    def route(url, kwargs):
        if "images/edits" in url:
            return _FakeResponse(200, json_data={"data": [{"b64_json": b64}]})
        if "files/write" in url:
            captured["save_json"] = kwargs.get("json")
            return _FakeResponse(200, json_data={"status": "SUCCESS"})
        return _FakeResponse(500, text_data=f"unexpected url: {url}")

    _patch_aiohttp(monkeypatch, route)

    req = _edit_req(proxy_url="http://proxy:11434")
    req.user_context = __import__("services.execution.schemas", fromlist=["UserContext"]).UserContext(
        user="default", is_admin=True
    )

    result = await image_edit_mod.handle_image_edit(req)

    assert result.status == "SUCCESS"
    save_json = captured["save_json"]
    assert isinstance(save_json["user_context"], dict)
    json.dumps(save_json)
    assert save_json["user_context"]["user"] == "default"
    assert save_json["user_context"]["is_admin"] is True
    assert save_json["relative_path"] == "sign_original_edited.jpg"
