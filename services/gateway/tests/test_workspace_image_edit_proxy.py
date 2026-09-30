"""Gateway proxy for workspace image editing.

A face swap is a two-image edit, so the donor face has to survive the hop from
the UI to the execution service. These tests pin that the donor is forwarded
under the key the handler actually reads (``face_image_path``) and that omitting
it stays a valid single-image edit.
"""
from contextlib import asynccontextmanager

from services.gateway import main as gateway_main


def _patch_exec(monkeypatch, status: int, payload, captured: dict):
    class _Resp:
        def __init__(self):
            self.status = status

        async def json(self):
            return payload

        async def text(self):
            return str(payload)

    class _Client:
        async def post(self, url, **kwargs):
            # shared_http_client is also used for shipping request logs, and that
            # call lands after the execution call, so only record the one under test.
            if "execute/image_edit" in url:
                captured["url"] = url
                captured["json"] = kwargs.get("json")
                captured["headers"] = kwargs.get("headers") or {}
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake_shared_client():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_shared_client)
    return captured


def test_the_donor_face_reaches_the_execution_handler(client, monkeypatch):
    captured = _patch_exec(
        monkeypatch,
        200,
        {"status": "SUCCESS", "detail": {"output_path": "out.png", "face_swapped": True}},
        {},
    )

    resp = client.post(
        "/api/workspaces/demo/images/edit",
        json={
            "image_path": "keep.jpg",
            "face_image_path": "donor.jpg",
            "prompt": "swap the face onto this photo",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["detail"]["face_swapped"] is True
    assert captured["url"].endswith("/execute/image_edit")
    # The key must match ImageEditRequest.face_image_path, or the donor is
    # silently dropped and the model edits the wrong photo.
    assert captured["json"]["face_image_path"] == "donor.jpg"
    assert captured["json"]["image_path"] == "keep.jpg"


def test_donor_path_alias_is_accepted(client, monkeypatch):
    captured = _patch_exec(monkeypatch, 200, {"status": "SUCCESS"}, {})
    client.post(
        "/api/workspaces/demo/images/edit",
        json={"image_path": "keep.jpg", "donor_path": "donor.jpg", "prompt": "swap"},
    )
    assert captured["json"]["face_image_path"] == "donor.jpg"


def test_a_plain_edit_sends_no_donor(client, monkeypatch):
    """Single-image edits must not grow a spurious donor field."""
    captured = _patch_exec(monkeypatch, 200, {"status": "SUCCESS"}, {})

    resp = client.post(
        "/api/workspaces/demo/images/edit",
        json={"image_path": "keep.jpg", "prompt": "make it look old"},
    )

    assert resp.status_code == 200
    assert captured["json"]["face_image_path"] is None


def test_the_execution_services_rejection_is_surfaced_verbatim(client, monkeypatch):
    """The handler's "needs a second image" message must reach the operator
    rather than being flattened into a generic failure."""
    message = (
        "Image edit failed: Face swap needs a second image: the model is only "
        "shown one image, so it cannot copy a face it was never given."
    )
    _patch_exec(monkeypatch, 200, {"status": "FAILURE", "message": message}, {})

    resp = client.post(
        "/api/workspaces/demo/images/edit",
        json={"image_path": "keep.jpg", "prompt": "swap the face"},
    )

    assert resp.status_code == 200
    assert resp.json()["message"] == message
