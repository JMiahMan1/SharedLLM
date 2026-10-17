"""Gateway proxy for workspace image editing.

A face swap is a two-image edit, so the donor face has to survive the hop from
the UI to the execution service. These tests pin that the donor is forwarded
under the key the handler actually reads (``face_image_path``) and that omitting
it stays a valid single-image edit.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest

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


# --- /api/images/edit: a picture carried in the request (drawings) ---------

_PNG_URL = "data:image/png;base64,iVBORw0KGgo="


def test_a_drawing_edit_runs_through_the_execution_service(client, monkeypatch):
    """The drawing pass goes to /execute/image_edit_inline, which resolves the
    configured model and llm_local_url proxy. It used to go straight to the
    legacy ALPACA_SD_URL with a JSON body and came back 502 every time."""
    captured = _patch_exec(
        monkeypatch,
        200,
        {"status": "SUCCESS", "detail": {"b64_json": "AAAA", "model": "qwen-image-edit"}},
        {},
    )

    resp = client.post("/api/images/edit", json={"prompt": "Make this drawing much better", "image": _PNG_URL})

    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS", "data": [{"b64_json": "AAAA"}], "model": "qwen-image-edit"}
    assert captured["url"].endswith("/execute/image_edit_inline")
    assert captured["json"]["image"] == _PNG_URL
    assert captured["json"]["prompt"] == "Make this drawing much better"
    assert "X-Internal-Secret" in captured["headers"]


def test_a_drawing_edit_failure_keeps_the_reason(client, monkeypatch):
    message = "Image edit failed: no image_edit_model configured."
    _patch_exec(monkeypatch, 200, {"status": "FAILURE", "message": message}, {})

    resp = client.post("/api/images/edit", json={"prompt": "better", "image": _PNG_URL})

    assert resp.status_code == 200
    assert resp.json() == {"status": "FAILURE", "message": message}


def test_a_drawing_edit_needs_a_picture_and_a_prompt(client, monkeypatch):
    captured = _patch_exec(monkeypatch, 200, {"status": "SUCCESS"}, {})

    resp = client.post("/api/images/edit", json={"prompt": "better"})

    assert resp.status_code == 400
    assert "url" not in captured


# --- /api/images/edit/jobs: the request returns before the edit finishes -----


@pytest.fixture(autouse=True)
def _clean_image_edit_jobs():
    gateway_main._IMAGE_EDIT_JOBS.clear()
    yield
    gateway_main._IMAGE_EDIT_JOBS.clear()
    gateway_main._BACKGROUND_TASKS.clear()


def _do_not_schedule(monkeypatch):
    """Keep the background task out of tests that drive the job explicitly."""
    monkeypatch.setattr(gateway_main, "_schedule_image_edit_job", lambda job_id: None)


def _no_picture_notice(monkeypatch):
    async def _noop(_user):
        return None

    monkeypatch.setattr(gateway_main.notices, "picture_ready", _noop)


def test_a_drawing_job_answers_at_once_and_finishes(client, monkeypatch):
    _do_not_schedule(monkeypatch)
    _no_picture_notice(monkeypatch)
    _patch_exec(
        monkeypatch,
        200,
        {"status": "SUCCESS", "detail": {"b64_json": "AAAA", "model": "qwen-image-edit"}},
        {},
    )

    started = client.post("/api/images/edit/jobs", json={"prompt": "better", "image": _PNG_URL})

    assert started.status_code == 202
    body = started.json()
    assert body["status"] == "PENDING"
    job_id = body["job_id"]
    assert len(job_id) == 32

    pending = client.get(f"/api/images/edit/jobs/{job_id}")
    assert pending.status_code == 200
    assert pending.json() == {"status": "PENDING", "job_id": job_id}

    asyncio.run(gateway_main._run_image_edit_job(job_id))

    done = client.get(f"/api/images/edit/jobs/{job_id}")
    assert done.status_code == 200
    assert done.json() == {
        "status": "SUCCESS",
        "job_id": job_id,
        "data": [{"b64_json": "AAAA"}],
        "model": "qwen-image-edit",
    }


def test_the_same_request_id_does_not_start_a_second_edit(client, monkeypatch):
    _do_not_schedule(monkeypatch)
    _patch_exec(monkeypatch, 200, {"status": "SUCCESS", "detail": {"b64_json": "AAAA"}}, {})
    body = {"prompt": "better", "image": _PNG_URL, "request_id": "tap-1"}

    first = client.post("/api/images/edit/jobs", json=body)
    second = client.post("/api/images/edit/jobs", json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(gateway_main._IMAGE_EDIT_JOBS) == 1


def test_a_failed_job_keeps_the_backends_reason(client, monkeypatch):
    _do_not_schedule(monkeypatch)
    _patch_exec(
        monkeypatch,
        200,
        {"status": "FAILURE", "message": "Image edit failed: no image_edit_model configured."},
        {},
    )

    started = client.post("/api/images/edit/jobs", json={"prompt": "better", "image": _PNG_URL})
    job_id = started.json()["job_id"]
    asyncio.run(gateway_main._run_image_edit_job(job_id))

    done = client.get(f"/api/images/edit/jobs/{job_id}")
    assert done.json()["status"] == "FAILURE"
    assert done.json()["message"] == "Image edit failed: no image_edit_model configured."


def test_a_job_needs_a_picture_and_a_prompt(client, monkeypatch):
    _do_not_schedule(monkeypatch)

    resp = client.post("/api/images/edit/jobs", json={"prompt": "better"})

    assert resp.status_code == 400


def test_an_unknown_job_is_a_404(client):
    resp = client.get(f"/api/images/edit/jobs/{'a' * 32}")

    assert resp.status_code == 404
