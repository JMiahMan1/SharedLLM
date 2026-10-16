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
    blew up with 'Object of type UserContext is not JSON serializable'; the
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


# --- Inline edits: the picture travels in the request (Family drawings) -----


def _png_data_url(size=(640, 416)) -> str:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color=(255, 255, 255)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _inline_req(**overrides):
    from services.execution.schemas import ImageEditInlineRequest

    fields = {"image": _png_data_url(), "prompt": "Make this drawing much better"}
    fields.update(overrides)
    return ImageEditInlineRequest(**fields)


@pytest.mark.asyncio
async def test_inline_edit_returns_the_picture_without_a_workspace(monkeypatch):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))
    sent = {}

    def route(url, kwargs):
        sent["url"] = url
        sent["form"] = kwargs.get("data")
        return _FakeResponse(200, json_data={"data": [{"b64_json": "RURJVEVE"}]})

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit_inline(_inline_req(proxy_url="http://proxy:11434/"))

    assert result.status == "SUCCESS"
    assert result.detail == {"b64_json": "RURJVEVE", "model": "qwen-image-edit-rapid-aio:q4_k", "size": "640x416"}
    assert sent["url"] == "http://proxy:11434/v1/images/edits"
    # The proxy takes multipart with the model named; a JSON body is refused.
    fields = {f[0]["name"]: f for f in sent["form"]._fields}
    assert {"model", "prompt", "size", "response_format", "image"} <= set(fields)


@pytest.mark.asyncio
async def test_inline_edit_without_a_model_names_the_setting(monkeypatch):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async(None))

    result = await image_edit_mod.handle_image_edit_inline(_inline_req())

    assert result.status == "FAILURE"
    assert "image_edit_model" in result.message


@pytest.mark.asyncio
async def test_inline_edit_rejects_something_that_is_not_a_picture(monkeypatch):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("m"))

    result = await image_edit_mod.handle_image_edit_inline(_inline_req(image="data:image/png;base64,???"))

    assert result.status == "FAILURE"
    assert "base64" in result.message


@pytest.mark.asyncio
async def test_inline_edit_reports_a_backend_refusal(monkeypatch):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("m"))
    _patch_aiohttp(monkeypatch, lambda url, kw: _FakeResponse(503, text_data="model not loaded"))

    result = await image_edit_mod.handle_image_edit_inline(_inline_req(proxy_url="http://proxy:11434"))

    assert result.status == "FAILURE"
    assert "503" in result.message and "model not loaded" in result.message


@pytest.mark.asyncio
async def test_inline_edit_reports_an_unreachable_backend(monkeypatch):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("m"))

    def route(url, kwargs):
        raise TimeoutError()

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit_inline(_inline_req(proxy_url="http://proxy:11434"))

    assert result.status == "FAILURE"
    assert "http://proxy:11434" in result.message and "TimeoutError" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("setting, expected", [("8", "8"), ("", None), ("fast", None), ("500", None)])
async def test_inline_edit_sends_the_configured_step_count(monkeypatch, setting, expected):
    monkeypatch.setattr(image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k"))

    async def fake_setting(key):
        return setting if key == "image_edit_steps" else None

    monkeypatch.setattr(image_edit_mod, "_identity_setting", fake_setting)
    sent = {}

    def route(url, kwargs):
        sent["form"] = kwargs.get("data")
        return _FakeResponse(200, json_data={"data": [{"b64_json": "RURJVEVE"}]})

    _patch_aiohttp(monkeypatch, route)
    await image_edit_mod.handle_image_edit_inline(_inline_req(proxy_url="http://proxy:11434/"))

    steps = [f[2] for f in sent["form"]._fields if f[0]["name"] == "steps"]
    assert steps == ([expected] if expected else [])
