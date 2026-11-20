"""Alpaca Vision for the garden: snap-to-add, the Plant Doctor and the journal.

One vision stack (execution's ``/execute/ocr``) with two new tasks; one photo
store (execution's ``/execute/photo_upload``) so the Garden never holds Nextcloud
credentials. Vision that is not configured fails loudly and names the setting.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import garden_routes, garden_vision
from services.gateway import main as gateway_main
from services.gateway import tool_registry
from services.gateway.main import app

LEAF = b"\xff\xd8\xff\xe0" + b"a-real-looking-jpeg" * 8


@pytest.fixture()
def client():
    return TestClient(app, headers={"Authorization": "Bearer adult-key"})


@pytest.fixture()
def identity(monkeypatch):
    async def acting(_request):
        return {"user": "kaleb", "is_admin": False, "family_role": "adult_child"}

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    return acting


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


# --------------------------------------------------------------------------- the vision call itself

class _Call:
    """A response that also works as ``async with client.post(...)``."""

    def __init__(self, resp: "_Resp"):
        self._resp = resp

    async def __aenter__(self) -> "_Resp":
        return self._resp

    async def __aexit__(self, *_exc) -> bool:
        return False

    def __await__(self):
        async def _self():
            return self._resp

        return _self().__await__()


def _exec_fake(monkeypatch, answer, status=200, raises: bool = False):
    sent: list[dict] = []

    class _Exec:
        def post(self, url, json=None, **kw):
            if raises:
                raise RuntimeError("execution is down")
            sent.append({"url": url, "json": json})
            return _Call(_Resp(answer, status=status))

        def get(self, url, **kw):  # pragma: no cover - not used by vision
            return _Call(_Resp(answer, status=status))

    @asynccontextmanager
    async def fake():
        yield _Exec()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return sent


def test_the_same_vision_pipeline_is_used_with_the_plant_task(monkeypatch):
    import asyncio

    sent = _exec_fake(monkeypatch, {
        "status": "SUCCESS",
        "detail": {"full_text": "A split-leaf plant.", "plant": {"common_name": "Monstera", "confidence": "high"}},
    })

    detail = asyncio.run(garden_vision.look(LEAF, task="plant_identify", creds={"user": "kaleb"}))

    assert detail["plant"]["common_name"] == "Monstera"
    assert sent[0]["json"]["task"] == "plant_identify"
    assert sent[0]["url"].endswith("/execute/ocr")


def test_the_diagnosis_sends_its_context(monkeypatch):
    import asyncio

    sent = _exec_fake(monkeypatch, {"status": "SUCCESS", "detail": {"diagnosis": {"summary": "Thirsty."}}})

    asyncio.run(
        garden_vision.look(LEAF, task="plant_diagnose", creds={"user": "kaleb"}, context="Plant: Monty")
    )

    assert sent[0]["json"]["context"] == "Plant: Monty"


def test_an_unconfigured_vision_model_fails_loudly(monkeypatch):
    import asyncio

    _exec_fake(monkeypatch, {"status": "FAILURE", "message": "OCR failed: vision_ocr_model not configured"})

    with pytest.raises(garden_vision.VisionUnavailable) as caught:
        asyncio.run(garden_vision.look(LEAF, task="plant_identify", creds={"user": "kaleb"}))

    assert "vision_ocr_model" in str(caught.value)


def test_a_vision_service_that_does_not_answer_is_admitted(monkeypatch):
    import asyncio

    _exec_fake(monkeypatch, {}, raises=True)

    with pytest.raises(garden_vision.VisionUnavailable):
        asyncio.run(garden_vision.look(LEAF, task="plant_identify", creds={"user": "kaleb"}))


def test_an_ordinary_vision_failure_is_not_a_configuration_error(monkeypatch):
    import asyncio

    _exec_fake(monkeypatch, {"status": "FAILURE", "message": "the photo is too dark"})

    with pytest.raises(garden_vision.VisionFailed) as caught:
        asyncio.run(garden_vision.look(LEAF, task="plant_identify", creds={"user": "kaleb"}))

    assert not isinstance(caught.value, garden_vision.VisionUnavailable)


def test_an_empty_photo_never_reaches_the_model():
    import asyncio

    with pytest.raises(garden_vision.VisionFailed):
        asyncio.run(garden_vision.look(b"", task="plant_identify", creds={"user": "kaleb"}))


# --------------------------------------------------------------------------- the routes

@pytest.fixture()
def garden(monkeypatch, identity):
    """A fake Garden service: species search, one plant, its photos, care logs."""
    state = {"posted": [], "deleted": []}

    async def call(method, path, *, params=None, json=None, headers=None):
        from fastapi.responses import Response
        import json as _json

        if path == "/species/search":
            query = (params or {}).get("q", "").lower()
            if "monstera" in query:
                return Response(
                    content=_json.dumps({
                        "query": query, "count": 1,
                        "results": [{"key": "monstera_deliciosa", "category": "houseplant", "common_name": "Monstera (Swiss Cheese Plant)"}],
                        "seed_present": True,
                    }),
                    media_type="application/json",
                )
            return Response(content=_json.dumps({"query": query, "count": 0, "results": [], "seed_present": True}), media_type="application/json")
        if path == "/plants/p-monty":
            return Response(
                content=_json.dumps({
                    "id": "p-monty", "nickname": "Monty", "category": "houseplant",
                    "species_ref": "monstera_deliciosa", "location_id": None,
                    "species": {"common_name": "Monstera", "light_level": "bright_indirect", "watering_interval_days": 7},
                }),
                media_type="application/json",
            )
        if path == "/plants/p-monty/logs":
            return Response(
                content=_json.dumps({"logs": [{"action": "water", "created_at": "2026-11-18T09:00:00+00:00", "performed_by": "kaleb"}]}),
                media_type="application/json",
            )
        if path == "/today":
            return Response(content=_json.dumps({"weather": {"summary": "Overnight low 31°F."}}), media_type="application/json")
        if path.endswith("/photos") and method == "POST":
            state["posted"].append(json)
            return Response(
                content=_json.dumps({
                    "id": "photo-1", "plant_id": "p-monty", "kind": (json or {}).get("kind"),
                    "nextcloud_path": (json or {}).get("nextcloud_path"),
                    "thumb_path": "photo-1.jpg" if (json or {}).get("thumb_base64") else None,
                    "notes": (json or {}).get("notes"),
                    "ai_summary_json": (json or {}).get("ai_summary_json"),
                    "taken_at": "2026-11-20T10:00:00+00:00", "taken_by": "kaleb",
                    "created_at": "2026-11-20T10:00:00+00:00",
                }),
                status_code=201,
                media_type="application/json",
            )
        if path == "/plants/p-monty/photos" and method == "GET":
            return Response(content=_json.dumps({"plant_id": "p-monty", "count": 1, "photos": [{"id": "photo-1", "kind": "progress"}]}), media_type="application/json")
        if path.endswith("/thumb"):
            return Response(content=b"\xff\xd8thumb", media_type="image/jpeg")
        if path.startswith("/plants/p-monty/photos/") and method == "DELETE":
            state["deleted"].append(path)
            return Response(status_code=204)
        return Response(content=_json.dumps({"detail": "not found"}), status_code=404, media_type="application/json")

    monkeypatch.setattr(garden_routes, "_call", call)
    return state


def test_snap_to_add_matches_the_seed_catalog(client, garden, monkeypatch):
    async def thinking(_raw, *, task, creds, context=None):
        return {
            "full_text": "A split-leaf tropical climber.",
            "plant": {
                "common_name": "Monstera", "scientific_name": "Monstera deliciosa",
                "category": "houseplant", "confidence": "high", "is_seed_packet": False,
            },
        }

    monkeypatch.setattr(garden_vision, "look", thinking)

    resp = client.post("/api/garden/vision/identify", files={"image": ("leaf.jpg", LEAF, "image/jpeg")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["identified"]["common_name"] == "Monstera"
    assert body["in_catalog"] is True
    assert body["matches"][0]["key"] == "monstera_deliciosa"
    # Nothing is created: the person adds the plant themselves.
    assert garden["posted"] == []


def test_a_plant_missing_from_the_catalog_offers_a_draft(client, garden, monkeypatch):
    async def thinking(_raw, *, task, creds, context=None):
        return {"plant": {"common_name": "Philodendron spiritus-sancti", "category": "houseplant", "confidence": "medium"}}

    monkeypatch.setattr(garden_vision, "look", thinking)

    body = client.post("/api/garden/vision/identify", files={"image": ("leaf.jpg", LEAF, "image/jpeg")}).json()

    assert body["in_catalog"] is False
    assert body["can_draft_profile"] is True


def test_identify_is_adults_only(client, garden, monkeypatch):
    async def child(_request):
        return {"user": "noah", "is_admin": False, "family_role": "child"}

    monkeypatch.setattr(gateway_main, "_acting_identity", child)

    resp = client.post("/api/garden/vision/identify", files={"image": ("leaf.jpg", LEAF, "image/jpeg")})

    assert resp.status_code == 403


def test_identify_without_vision_says_which_setting_to_set(client, garden, monkeypatch):
    async def unconfigured(_raw, *, task, creds, context=None):
        raise garden_vision.VisionUnavailable("OCR failed: vision_ocr_model not configured in Identity settings")

    monkeypatch.setattr(garden_vision, "look", unconfigured)

    resp = client.post("/api/garden/vision/identify", files={"image": ("leaf.jpg", LEAF, "image/jpeg")})

    assert resp.status_code == 503
    assert "vision_ocr_model" in resp.json()["detail"]


def test_the_plant_doctor_saves_the_diagnosis_on_the_timeline(client, garden, monkeypatch):
    seen: dict = {}

    async def thinking(_raw, *, task, creds, context=None):
        seen["context"] = context
        return {
            "diagnosis": {
                "likely_causes": [{"cause": "overwatering", "confidence": "high", "why": "yellow lower leaves"}],
                "confidence": "high", "urgency": "medium",
                "recovery_steps": ["Let the top 2 inches dry out", "Check the drainage holes"],
                "care_adjustments": ["Water every 10 days instead of 7"],
                "summary": "Too much water. Let it dry out.",
            },
            "uncertain": ["root health"],
        }

    async def stored(_raw, *, creds, plant_id, thumbnail_max_px=480):
        return {"nextcloud_path": f"Garden/Photos/{plant_id}/abc.jpg", "thumb_base64": "dGh1bWI=", "warning": None}

    monkeypatch.setattr(garden_vision, "look", thinking)
    monkeypatch.setattr(garden_vision, "upload_photo", stored)

    resp = client.post(
        "/api/garden/plants/p-monty/diagnose",
        files={"image": ("leaf.jpg", LEAF, "image/jpeg")},
        data={"symptoms": "yellow lower leaves"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["diagnosis"]["urgency"] == "medium"
    assert body["diagnosis"]["recovery_steps"] == ["Let the top 2 inches dry out", "Check the drainage holes"]
    # The plant's own history and the family's words reached the model.
    assert "Monty" in seen["context"] and "yellow lower leaves" in seen["context"]
    # And the diagnosis lives on the plant's timeline.
    assert garden["posted"][0]["kind"] == "diagnosis"
    assert "overwatering" in garden["posted"][0]["ai_summary_json"]
    assert garden["posted"][0]["nextcloud_path"].startswith("Garden/Photos/p-monty/")


def test_a_progress_photo_keeps_working_when_nextcloud_is_not_configured(client, garden, monkeypatch):
    async def no_nextcloud(_raw, *, creds, plant_id, thumbnail_max_px=480):
        return {
            "nextcloud_path": None, "thumb_base64": None,
            "warning": "No Nextcloud account is connected, so the photo was not stored.",
        }

    monkeypatch.setattr(garden_vision, "upload_photo", no_nextcloud)

    resp = client.post(
        "/api/garden/plants/p-monty/photos",
        files={"image": ("growth.jpg", LEAF, "image/jpeg")},
        data={"notes": "New leaf unfurling"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["photo"]["kind"] == "progress"
    assert "Nextcloud" in body["nextcloud_warning"]
    assert garden["posted"][0]["notes"] == "New leaf unfurling"


def test_a_photo_without_an_image_is_refused(client, garden):
    resp = client.post("/api/garden/plants/p-monty/photos", data={"notes": "no photo"})

    assert resp.status_code == 422
    assert "image" in resp.json()["detail"]


def test_the_journal_is_listed_and_a_photo_deleted_through_the_proxy(client, garden):
    listed = client.get("/api/garden/plants/p-monty/photos")
    assert listed.status_code == 200
    assert listed.json()["photos"][0]["id"] == "photo-1"

    deleted = client.delete("/api/garden/plants/p-monty/photos/photo-1")
    assert deleted.status_code == 204
    assert garden["deleted"] == ["/plants/p-monty/photos/photo-1"]


def test_the_thumbnail_streams_as_an_image(client, garden, monkeypatch):
    # The thumb route streams bytes itself (a JSON proxy would corrupt them), so
    # the Garden's answer is faked at the HTTP edge.
    class _Bin:
        status = 200
        headers = {"Content-Type": "image/jpeg"}

        async def read(self):
            return b"\xff\xd8thumb"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class _Client:
        def get(self, *_args, **_kw):
            return _Bin()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)

    resp = client.get("/api/garden/plants/p-monty/photos/photo-1/thumb")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content.startswith(b"\xff\xd8")


# --------------------------------------------------------------------------- the Librarian tools

def test_the_six_garden_tools_are_offered():
    names = {schema["function"]["name"] for schema in tool_registry.get_tool_schemas()}

    assert {
        "garden_summary", "garden_log_care", "garden_log_harvest",
        "garden_recipe_matches", "garden_preservation_guide", "garden_plan_suggest",
    } <= names


def test_a_garden_tool_resolves_to_its_gateway_route():
    call = tool_registry.resolve_tool_call("garden_log_care", {"target": "Monty", "action": "water"})

    assert call.service == "gateway"
    assert call.path == "/api/garden/assistant/log-care"
    assert call.json["target"] == "Monty"


def test_a_summary_keeps_the_fields_worth_showing(client, garden, monkeypatch):
    summary = {"likely_causes": [{"cause": "overwatering"}], "urgency": "high", "summary": "Act now."}

    assert "overwatering" in garden_vision.as_summary_json(summary)
    assert "urgency" in garden_vision.as_summary_json(summary)
