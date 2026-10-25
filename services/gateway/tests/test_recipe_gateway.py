"""Recipe photo import: the gateway reads a recipe photo through the vision OCR."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _ocr_fake(monkeypatch, answer, status=200):
    sent: list[dict] = []

    async def _creds(_request, _as_user=None):
        return {"user": "dad", "is_admin": False}

    class _Exec:
        async def post(self, url, json=None, **kw):
            sent.append({"url": url, "json": json})
            return _Resp(answer, status=status)

    @asynccontextmanager
    async def fake():
        yield _Exec()

    monkeypatch.setattr(gateway_main, "_resolve_acting_identity", _creds)
    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return sent


def test_a_recipe_photo_becomes_a_draft_and_nothing_is_saved(client, monkeypatch):
    sent = _ocr_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "detail": {
                "full_text": "Nana's Pancakes\n2 cups flour",
                "recipe": {
                    "title": "Nana's Pancakes",
                    "servings": "4",
                    "ingredients": ["2 cups flour", "1 tsp salt"],
                    "steps": ["Mix.", "Bake."],
                },
                "uncertain": ["salt (line 3)"],
            },
        },
    )
    body = client.post(
        "/api/recipes/photo", files={"image": ("page.jpg", b"jpeg-bytes", "image/jpeg")}
    ).json()
    assert body["recipe"]["title"] == "Nana's Pancakes"
    assert body["recipe"]["ingredients"] == ["2 cups flour", "1 tsp salt"]
    assert body["uncertain"] == ["salt (line 3)"]
    assert body["missing"] == []
    assert body["raw_text"].startswith("Nana's Pancakes")
    assert sent[0]["url"].endswith("/execute/ocr")
    assert sent[0]["json"]["task"] == "recipe"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_an_unreadable_photo_says_so_and_a_missing_one_is_refused(client, monkeypatch):
    _ocr_fake(
        monkeypatch,
        {"status": "FAILURE", "message": "OCR failed: vision_ocr_model not configured"},
    )
    response = client.post(
        "/api/recipes/photo", files={"image": ("page.jpg", b"jpeg-bytes", "image/jpeg")}
    )
    assert response.status_code == 502
    assert "vision_ocr_model" in response.json()["detail"]
    assert client.post(
        "/api/recipes/photo", files={"other": ("x.jpg", b"x", "image/jpeg")}
    ).status_code == 422
