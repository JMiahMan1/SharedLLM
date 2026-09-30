"""Face swap is a TWO-image edit, so these tests pin the behaviour that makes it
possible at all and the guard that stops a swap silently editing the wrong photo.

The regression these guard against: a swap prompt with no donor image used to be
sent to the model with a single photo, which returns a plausible-looking edited
image of the *wrong person* reported as success.
"""
import base64
from pathlib import Path

import pytest

from services.execution.handlers import image_edit as image_edit_mod
from services.execution.schemas import ImageEditRequest


def _async(value):
    async def _inner():
        return value

    return _inner


def _make_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 48), color=(30, 90, 160)).save(path)


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


def _edit_req(**overrides):
    fields = {
        "user_context": {"user": "default", "is_admin": True},
        "workspace_id": "Test",
        "image_path": "keep.jpg",
        "prompt": "make it look old",
    }
    fields.update(overrides)
    return ImageEditRequest(**fields)


def _stub_workspace(monkeypatch, tmpdir):
    async def fake_resolve(ws, uc):
        return str(tmpdir), {}

    monkeypatch.setattr(image_edit_mod, "_resolve_workspace_info", fake_resolve)
    monkeypatch.setattr(
        image_edit_mod, "get_image_edit_model", _async("qwen-image-edit-rapid-aio:q4_k")
    )


def _ok_route(captured):
    b64 = base64.b64encode(b"fake-edited-png-bytes").decode()

    def route(url, kwargs):
        if "images/edits" in url:
            captured.update(kwargs)
            return _FakeResponse(200, json_data={"data": [{"b64_json": b64}]})
        if "files/write" in url:
            return _FakeResponse(200, json_data={"status": "SUCCESS"})
        return _FakeResponse(500, text_data=f"unexpected url: {url}")

    return route


def _form_fields(kwargs):
    """aiohttp FormData._fields holds (headers, options, value) tuples, where the
    part name and filename live in the headers MultiDict."""
    form = kwargs["data"]
    return [(f[0].get("name"), f[2], f[0].get("filename")) for f in form._fields]



# --------------------------------------------------------------------------
# looks_like_face_swap: the detector that decides a donor is mandatory
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "swap the faces",
        "Swap his face for hers",
        "swap the face onto the other photo",
        "transplant the face from the donor",
        "put bob's face on the man",
        "place her face onto this portrait",
        "use the face from the second photo",
        "transfer the face onto the body",
    ],
)
def test_face_swap_prompts_are_detected(prompt):
    assert image_edit_mod.looks_like_face_swap(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        # Ordinary single-image edits that must NOT be blocked.
        "make it look old",
        "remove the website link on the sign",
        "convert to sepia",
        # A bare "replace" is a single-image edit, not a swap: blocking these
        # would be a regression on a legitimate use.
        "replace the face with a smiley",
        "replace the face on the robot with chrome",
        # Face mentioned, but no transfer verb.
        "sharpen the face",
        "make the faces in the background blurry",
        "add a face to the drawing",
    ],
)
def test_ordinary_edit_prompts_are_not_flagged_as_swaps(prompt):
    assert image_edit_mod.looks_like_face_swap(prompt) is False


@pytest.mark.parametrize("prompt", [None, "", "   "])
def test_empty_prompt_is_not_a_swap(prompt):
    assert image_edit_mod.looks_like_face_swap(prompt) is False


# --------------------------------------------------------------------------
# The guard: a swap with no donor must fail loudly, not edit the wrong photo
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_swap_prompt_with_no_donor_is_rejected_with_a_clear_fix(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "keep.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    def route(url, kwargs):
        raise AssertionError("must not reach the model without a donor image")

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(
        _edit_req(prompt="swap the face onto this photo", proxy_url="http://proxy:11434")
    )

    assert result.status == "FAILURE"
    # The message has to name the field to set, not just complain.
    assert "face_image_path" in result.message
    assert "second image" in result.message.lower()


@pytest.mark.asyncio
async def test_an_ordinary_edit_with_no_donor_still_works(tmpdir, monkeypatch):
    """The guard must not break the single-image path that everything else uses."""
    _make_png(Path(tmpdir) / "keep.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    captured = {}
    _patch_aiohttp(monkeypatch, _ok_route(captured))

    result = await image_edit_mod.handle_image_edit(
        _edit_req(prompt="make it look old", proxy_url="http://proxy:11434")
    )

    assert result.status == "SUCCESS"
    names = [n for n, _v, _fn in _form_fields(captured)]
    assert names.count("image") == 1, "a plain edit must send exactly one image"
    assert result.detail["face_swapped"] is False
    assert result.detail["face_image_path"] is None


# --------------------------------------------------------------------------
# The feature: two images, roles stated, donor validated
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_swap_sends_both_images_and_states_which_is_which(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "keep.jpg")
    _make_png(Path(tmpdir) / "donor.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    captured = {}
    _patch_aiohttp(monkeypatch, _ok_route(captured))

    result = await image_edit_mod.handle_image_edit(
        _edit_req(
            prompt="swap the face onto this photo",
            face_image_path="donor.jpg",
            proxy_url="http://proxy:11434",
        )
    )

    assert result.status == "SUCCESS"
    fields = _form_fields(captured)
    images = [fn for n, _v, fn in fields if n == "image"]
    assert len(images) == 2, f"a swap must send two images, got {images}"
    # Base first, donor second (OpenAI images/edits array order).
    assert images == ["keep.jpg", "donor.jpg"]

    prompt = next(v for n, v, _fn in fields if n == "prompt")
    # The model must be told which image is which, or it guesses.
    assert "Image 1" in prompt and "Image 2" in prompt
    assert "swap the face onto this photo" in prompt, "user's own wording is preserved"

    assert result.detail["face_swapped"] is True
    assert result.detail["face_image_path"] == "donor.jpg"
    assert "Face swapped" in result.message


@pytest.mark.asyncio
async def test_a_missing_donor_reports_the_path_it_looked_for(tmpdir, monkeypatch):
    _make_png(Path(tmpdir) / "keep.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    def route(url, kwargs):
        raise AssertionError("must not reach the model with a missing donor")

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(
        _edit_req(
            prompt="swap the face",
            face_image_path="nope.jpg",
            proxy_url="http://proxy:11434",
        )
    )

    assert result.status == "FAILURE"
    assert "nope.jpg" in result.message


@pytest.mark.asyncio
async def test_a_donor_outside_the_workspace_is_refused(tmpdir, monkeypatch):
    """The donor is a second attacker-controlled path, so it gets the same
    traversal containment as the source image."""
    _make_png(Path(tmpdir) / "keep.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    def route(url, kwargs):
        raise AssertionError("must not reach the model with an escaped donor")

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(
        _edit_req(
            prompt="swap the face",
            face_image_path="../../../etc/passwd",
            proxy_url="http://proxy:11434",
        )
    )

    assert result.status == "FAILURE"
    assert "traversal" in result.message.lower()


@pytest.mark.asyncio
async def test_a_donor_that_is_the_source_image_is_refused(tmpdir, monkeypatch):
    """Swapping a photo's face with its own face is always a mistake."""
    _make_png(Path(tmpdir) / "keep.jpg")
    _stub_workspace(monkeypatch, tmpdir)

    def route(url, kwargs):
        raise AssertionError("must not reach the model with donor == source")

    _patch_aiohttp(monkeypatch, route)

    result = await image_edit_mod.handle_image_edit(
        _edit_req(
            prompt="swap the face",
            face_image_path="keep.jpg",
            proxy_url="http://proxy:11434",
        )
    )

    assert result.status == "FAILURE"
    assert "same file" in result.message
