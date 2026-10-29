"""Recipe photo import: the gateway reads a recipe photo through the vision OCR."""
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

#: The real photos kept for the import tests: a handwritten card and a label.
RECIPE_PHOTOS = Path(__file__).resolve().parents[3] / "test" / "fixtures" / "recipes"


def _photo(name: str) -> bytes:
    """The actual bytes of a kept photo, so the upload path is exercised for real."""
    return (RECIPE_PHOTOS / name).read_bytes()


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


def test_a_typed_recipe_card_photo_round_trips_through_the_gateway(client, monkeypatch):
    sent = _ocr_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "detail": {
                "full_text": "Grandma's Banana Bread\nServes 8\nPrep 15 min | Cook 55 min",
                "recipe": {
                    "title": "Grandma's Banana Bread",
                    "servings": "8",
                    "prep_minutes": 15,
                    "cook_minutes": 55,
                    "total_minutes": 70,
                    "ingredients": ["3 ripe bananas", "1/2 cup butter", "3/4 cup sugar"],
                    "steps": ["Mash the bananas.", "Bake for 55 minutes."],
                },
                "uncertain": [],
            },
        },
    )
    body = client.post(
        "/api/recipes/photo", files={"image": ("card.jpg", b"jpeg-bytes", "image/jpeg")}
    ).json()
    assert body["recipe"]["title"] == "Grandma's Banana Bread"
    assert body["recipe"]["prep_minutes"] == 15 and body["recipe"]["cook_minutes"] == 55
    assert body["recipe"]["yield"] == "8"
    assert body["missing"] == [] and body["uncertain"] == []
    assert sent[0]["json"]["task"] == "recipe"


def test_a_handwritten_recipe_photo_names_what_could_not_be_read(client, monkeypatch):
    _ocr_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "detail": {
                "full_text": "Ma's Chili\n1 lb ground beef\n...simmer",
                "recipe": {
                    "title": "Ma's Chili",
                    "ingredients": ["1 lb ground beef", "1 onion"],
                    "steps": [],
                },
                "uncertain": ["simmer (line 8)"],
            },
        },
    )
    body = client.post(
        "/api/recipes/photo", files={"image": ("card.jpg", b"jpeg-bytes", "image/jpeg")}
    ).json()
    assert body["recipe"]["title"] == "Ma's Chili"
    assert body["recipe"]["instructions"] == []
    assert body["missing"] == ["steps"]
    assert body["uncertain"] == ["simmer (line 8)"]


def test_the_real_handwritten_card_photo_travels_through_the_upload_path(client, monkeypatch):
    """The picture the family actually scans goes through the same path: its real
    bytes are base64'd onto the OCR call and the draft comes back to the page."""
    image = _photo("sugar-cookies-card.png")
    sent = _ocr_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "detail": {
                "full_text": (
                    "Sugar Cookies  Ann.\n1 cup sugar\n1 cup br. sugar\n1 cup shortening\n"
                    "2 eggs\n2 teaspoon cr. of tartar\n2 teaspoon vanilla\n1 teaspoon soda\n"
                    "2 1/4 cup flour - more if needed\n"
                    "Roll in balls dip in cold water then in sugar. Bake at 350 15 min."
                ),
                "recipe": {
                    "title": "Sugar Cookies",
                    "servings": None,
                    "ingredients": [
                        "1 cup sugar",
                        "1 cup br. sugar",
                        "1 cup shortening",
                        "2 eggs",
                        "2 teaspoon cr. of tartar",
                        "2 teaspoon vanilla",
                        "1 teaspoon soda",
                        "2 1/4 cup flour - more if needed",
                    ],
                    "steps": ["Roll in balls, dip in cold water then in sugar.", "Bake at 350 for 15 min."],
                },
                "uncertain": ["br. (line 2)", "cr. (line 5)"],
            },
        },
    )
    body = client.post(
        "/api/recipes/photo",
        files={"image": ("sugar-cookies-card.png", image, "image/png")},
    ).json()
    assert body["recipe"]["title"] == "Sugar Cookies"
    assert len(body["recipe"]["ingredients"]) == 8
    assert body["recipe"]["instructions"] == [
        "Roll in balls, dip in cold water then in sugar.",
        "Bake at 350 for 15 min.",
    ]
    assert body["missing"] == []
    assert body["uncertain"] == ["br. (line 2)", "cr. (line 5)"]
    # The real photo travelled, whole, base64-encoded, on the OCR call.
    assert base64.b64decode(sent[0]["json"]["image_base64"]) == image
    assert sent[0]["json"]["task"] == "recipe"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_the_real_photos_are_readable_images():
    """The kept photos are genuine images the OCR pipeline can open, not placeholders."""
    from PIL import Image

    for name in ("sugar-cookies-card.png", "food-label.webp"):
        with Image.open(RECIPE_PHOTOS / name) as im:
            assert im.width > 100 and im.height > 100


class _ProxyResp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def text(self):
        return json.dumps(self._body)


def _proxy_fake(monkeypatch, answer, status=200):
    sent: list[dict] = []

    async def _creds(_request, _as_user=None):
        return {"user": "dad", "is_admin": False}

    class _Exec:
        async def post(self, url, json=None, **kw):
            sent.append({"url": url, "json": json})
            return _ProxyResp(answer, status=status)

    @asynccontextmanager
    async def fake():
        yield _Exec()

    monkeypatch.setattr(gateway_main, "_resolve_acting_identity", _creds)
    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return sent


def test_recipe_sources_proxies_the_duplicate_guard(client, monkeypatch):
    sent = _proxy_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "message": "1 recipe(s) already imported.",
            "service": "recipe_sources",
            "detail": {"sources": ["https://www.food.com/recipe/chili-1"], "notes": 2},
        },
    )
    body = client.post("/api/recipes/sources", json={}).json()
    assert body["detail"]["sources"] == ["https://www.food.com/recipe/chili-1"]
    assert sent[0]["url"].endswith("/execute/recipes")
    assert sent[0]["json"]["action"] == "recipe_sources"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_planning_a_meal_forwards_the_sentence_and_identity(client, monkeypatch):
    sent = _proxy_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "message": "Planned Slow Cooker Chicken Chili for Tue.",
            "service": "meal_plan",
            "detail": {"day": "Tue", "recipe": "Slow Cooker Chicken Chili"},
        },
    )
    body = client.post(
        "/api/recipes/plan",
        json={"text": "add a slow-cooker meal to Tuesday"},
    ).json()
    assert body["detail"]["day"] == "Tue"
    assert sent[0]["url"].endswith("/execute/meal_plan")
    assert sent[0]["json"]["text"] == "add a slow-cooker meal to Tuesday"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_planning_a_meal_passes_an_already_resolved_day_and_recipe(client, monkeypatch):
    sent = _proxy_fake(monkeypatch, {"status": "SUCCESS", "message": "Planned Pizza for Fri."})
    client.post("/api/recipes/plan", json={"text": "plan something", "day": "Fri", "recipe": "Pizza"})
    assert sent[0]["json"]["day"] == "Fri"
    assert sent[0]["json"]["recipe"] == "Pizza"


def test_staple_ranking_proxies_the_shelf_scan(client, monkeypatch):
    sent = _proxy_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "message": "Ranked 2 recipe(s); the best uses 3 staple(s).",
            "service": "recipe_staples",
            "detail": {"ranked": [{"title": "Chili", "path": "Recipes/Chili.md", "score": 3, "staples": ["beans"]}], "notes": 2},
        },
    )
    body = client.post("/api/recipes/staples", json={}).json()
    assert body["detail"]["ranked"][0]["title"] == "Chili"
    assert sent[0]["url"].endswith("/execute/recipes")
    assert sent[0]["json"]["action"] == "recipe_staples"
    assert sent[0]["json"]["user_context"]["user"] == "dad"


def test_the_ics_fallback_forwards_the_action(client, monkeypatch):
    sent = _proxy_fake(
        monkeypatch,
        {
            "status": "SUCCESS",
            "message": "Ready to publish 2 dinner(s) to a calendar.",
            "service": "meal_plan",
            "detail": {"ics": "BEGIN:VCALENDAR\nEND:VCALENDAR", "events": 2},
        },
    )
    body = client.post("/api/recipes/plan/ics", json={}).json()
    assert body["detail"]["events"] == 2
    assert sent[0]["url"].endswith("/execute/meal_plan")
    assert sent[0]["json"]["action"] == "ics"
    assert sent[0]["json"]["user_context"]["user"] == "dad"
