"""The browsing shelf, ratings and recipe photos."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import cookbook, main, recipe_images


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.jpg", "http://192.168.2.205:8080/a.png", "http://localhost/a.jpg",
    "file:///etc/passwd", "http://user:pw@example.com/a.jpg", "http://169.254.169.254/latest",
])
async def test_photos_are_only_fetched_from_public_addresses(url):
    with pytest.raises(recipe_images.ImageRefused):
        await recipe_images.check_url(url)


def test_the_catalog_merges_the_family_stats(monkeypatch):
    house = {"user": "house", "is_admin": True}

    async def caller(request, as_user=None):
        return cookbook.Caller(user="mom", is_admin=False, house="house", house_creds=house, own_creds={"user": "mom"})

    class Resp:
        status = 200

        async def text(self):
            import json
            return json.dumps({"status": "SUCCESS", "detail": {"recipes": [
                {"title": "Chili", "path": "Recipes/Chili.md", "owner": "mom", "meal": "soup", "proteins": ["chicken"]},
                {"title": "Old Soup", "path": "Recipes/Old-Soup.md", "owner": None, "meal": "soup", "proteins": ["vegetarian"]},
            ]}})

    class Client:
        async def post(self, url, json=None, **kw):
            assert json["user_context"] == house and json["action"] == "recipe_catalog"
            return Resp()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def http():
        yield Client()

    async def health(method, path, request, **kw):
        return {"recipes": {"Recipes/Chili.md": {"rating": 4.5, "ratings": 2, "mine": 5, "made": 3, "made_by_me": 1, "last_made": "2026-10-30"}}}

    monkeypatch.setattr(main, "_cookbook_caller", caller)
    monkeypatch.setattr(main, "shared_http_client", http)
    monkeypatch.setattr(main, "_health_json", health)
    body = TestClient(main.app).post("/api/recipes/catalog", json={}).json()
    chili, soup = body["recipes"]
    assert chili["rating"] == 4.5 and chili["made"] == 3 and chili["my_rating"] == 5 and chili["can_change"] is True
    assert soup["owner"] == "house" and soup["owner_label"] == "an admin" and soup["can_change"] is False
    assert soup["made"] == 0 and soup["rating"] is None


def test_a_photo_line_survives_an_edit_that_does_not_mention_it():
    body = cookbook.with_owner("Tags: #x\n\n## Steps\n1. go", "mom", ".attachments.7/photo.jpg")
    assert cookbook.recipe_image(body) == ".attachments.7/photo.jpg"
    assert "Image:" not in cookbook.without_owner(body)
