"""The morning digest's garden buttons: tapping "Watered" must actually work.

Every one of these taps used to answer HTTP 500 with "Object of type Response
is not JSON serializable": the proxy handed a Starlette ``Response`` back to a
handler that builds its own JSON body. The proxy decodes now, and a refusal
becomes an ``HTTPException`` with the Garden's own status.
"""
from __future__ import annotations

import asyncio
import json
import json as _json

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from services.gateway import garden_routes, push_callbacks


@pytest.fixture()
def garden(monkeypatch):
    """The Garden service as the digest callbacks see it: `_call` + identity."""
    seen: list[tuple[str, str, dict | None, dict | None]] = []
    reply_status = {"value": 200}

    async def headers(_request):
        return {"X-User-Id": "mother"}

    async def call(method, path, *, params=None, json=None, headers=None):
        seen.append((method, path, params, json))
        # A plant that is gone fails on its own; the flag fails every call.
        if reply_status["value"] >= 400 or "/gone/" in path:
            return Response(
                content=_json.dumps({"detail": "Plant not found"}).encode(),
                status_code=reply_status["value"] if reply_status["value"] >= 400 else 404,
                media_type="application/json",
            )
        body = {"ok": True, "plant_id": path.split("/")[2], "next_due_at": None}
        return Response(content=_json.dumps(body).encode(), media_type="application/json")

    monkeypatch.setattr(garden_routes, "_verified_headers", headers)
    monkeypatch.setattr(garden_routes, "_call", call)
    return {"seen": seen, "status": reply_status}


def test_the_garden_buttons_are_registered():
    assert "garden_done" in push_callbacks.PUSH_CALLBACKS
    assert "garden_snooze" in push_callbacks.PUSH_CALLBACKS


def test_tapping_watered_on_one_plant_logs_care(garden):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_done"](
            object(), {"plant_id": "p-1", "action": "water"}, None
        )
    )

    assert body["ok"] is True
    assert body["plant_id"] == "p-1"
    method, path, _params, payload = garden["seen"][0]
    assert (method, path) == ("POST", "/plants/p-1/care")
    assert payload == {"action": "water", "source": "push_action"}


def test_tapping_watered_on_several_plants_answers_json(garden):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_done"](
            object(), {"plant_ids": ["p-1", "p-2"], "action": "water"}, None
        )
    )

    # The answer is serializable: that is what used to crash.
    assert json.dumps(body)
    assert [row["plant_id"] for row in body["results"]] == ["p-1", "p-2"]
    assert [path for _method, path, _params, _json in garden["seen"]] == [
        "/plants/p-1/care",
        "/plants/p-2/care",
    ]


def test_tapping_still_damp_snoozes_the_plant(garden):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_snooze"](
            object(), {"plant_id": "p-1", "postpone_days": 3}, None
        )
    )

    assert body["plant_id"] == "p-1"
    method, path, params, _payload = garden["seen"][0]
    assert (method, path) == ("POST", "/plants/p-1/snooze")
    assert params == {"still_damp": "true", "postpone_days": "3"}


def test_snoozing_several_plants_answers_json(garden):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_snooze"](object(), {"plant_ids": ["p-1", "p-2"]}, None)
    )

    assert json.dumps(body)
    assert len(body["results"]) == 2


def test_a_garden_refusal_becomes_the_error_not_a_fake_success(garden):
    garden["status"]["value"] = 404

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            push_callbacks.PUSH_CALLBACKS["garden_done"](object(), {"plant_id": "gone"}, None)
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "Plant not found"


def test_one_bad_plant_does_not_stop_the_rest(garden):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_done"](object(), {"plant_ids": ["gone", "p-2"]}, None)
    )

    assert json.dumps(body)
    assert [row.get("error") for row in body["results"]] == ["Plant not found", None]
    assert [row["ok"] for row in body["results"]] == [False, True]
    assert (body["done"], body["failed"]) == (1, 1)


def test_a_tap_without_a_plant_is_refused():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(push_callbacks.PUSH_CALLBACKS["garden_done"](object(), {}, None))

    assert caught.value.status_code == 422
