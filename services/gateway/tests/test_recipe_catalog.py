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


@pytest.mark.asyncio
async def test_a_food_com_photo_comes_from_the_index_before_the_page(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, body):
            self.body = body

        async def text(self):
            import json
            return json.dumps(self.body)

    class Client:
        async def post(self, url, json=None, **kw):
            sent.append(json["action"])
            image = "https://img/7.jpg" if json["action"] == "shelf_fetch" and json["id"] == 12345 else None
            return Resp({"status": "SUCCESS", "detail": {"recipe": {"image": image}}})

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def http():
        yield Client()

    monkeypatch.setattr(main, "shared_http_client", http)
    caller = cookbook.Caller(user="dad", is_admin=True, house="house", house_creds={"user": "house"}, own_creds={})
    assert await main._source_photo(caller, "https://www.food.com/recipe/white-chili-12345") == "https://img/7.jpg"
    assert sent == ["shelf_fetch"]
    sent.clear()
    assert await main._source_photo(caller, "https://example.com/chili") is None
    assert sent == ["url_import"]


def test_a_recipe_read_from_a_photo_shows_that_photo_and_matches_by_any_spelling():
    body = "Tags: #photo-import\n\n## Notes\nChill.\n![card.jpg](.attachments.7/card.jpg)\n"
    assert cookbook.recipe_picture(body) == ".attachments.7/card.jpg"
    assert cookbook.recipe_picture("Image: .attachments.7/dish.jpg\n" + body) == ".attachments.7/dish.jpg"
    assert cookbook.title_key("Christmas-Sugar-Cookies") == cookbook.title_key("christmas sugar  cookies")


def test_a_bulk_imported_note_keeps_its_name_when_its_photo_is_set():
    # The importer wrote a blank line between the title and Category lines.
    text = "# 20 Minute White Bean Chili\n\nCategory: Recipes\n\nTags: #soup\n"
    assert cookbook.stored_title(text) == "20 Minute White Bean Chili"
    assert cookbook.strip_stored_header(text) == "Tags: #soup\n"
    assert cookbook.readable_title("beef-and-bean-burritos") == "Beef and Bean Burritos"
    assert cookbook.readable_title("Mom's Chili-Mac") == "Mom's Chili-Mac"


def test_a_new_recipe_is_named_in_the_cookbook_style():
    assert cookbook.tidy_title("quick-beef-stew") == "Quick Beef Stew"
    assert cookbook.tidy_title("  slow cooker chili with beans ") == "Slow Cooker Chili with Beans"
    assert cookbook.tidy_title("Mom's Chili-Mac") == "Mom's Chili-Mac"
    assert cookbook.tidy_title("BLT Pasta") == "BLT Pasta"


class _Shelf:
    """A one-recipe cookbook in memory, answering the note actions the photo functions use."""

    def __init__(self, body: str):
        self.text = "# Chili\nCategory: Recipes\n" + body
        self.deleted: list[str] = []

    async def __call__(self, creds, payload):
        action = payload["action"]
        if action == "read":
            return {"status": "SUCCESS", "message": self.text, "detail": {"etag": '"1"'}}
        if action == "attachment_upload":
            return {"status": "SUCCESS", "detail": {"link": ".attachments.9/new.jpg"}}
        if action == "write":
            self.text = f"# {payload['title']}\nCategory: Recipes\n" + payload["content"]
            return {"status": "SUCCESS", "detail": {"path": payload["path"]}}
        if action == "attachment_delete":
            self.deleted.append(payload["attachment"])
            return {"status": "SUCCESS"}
        raise AssertionError(action)


def _who(user: str, admin: bool = False) -> cookbook.Caller:
    return cookbook.Caller(user=user, is_admin=admin, house="house", house_creds={"user": "house"}, own_creds={"user": user})


@pytest.mark.asyncio
async def test_anyone_adds_a_photo_and_the_first_becomes_the_cover():
    shelf = _Shelf("Added by: mom\n\n## Steps\n1. Cook.\n")
    res = await cookbook.add_photo(shelf, _who("kid"), "Recipes/Chili.md", "dinner.jpg", "eA==")
    assert res["status"] == "SUCCESS"
    body = cookbook.strip_stored_header(shelf.text)
    assert "Image: .attachments.9/new.jpg" in body and "![Photo by kid](.attachments.9/new.jpg)" in body
    assert cookbook.recipe_owner(body, "house") == "mom"


@pytest.mark.asyncio
async def test_only_the_owner_picks_the_cover_or_removes_a_photo():
    body = "Image: .attachments.9/a.jpg\nAdded by: mom\n\n## Steps\n1. Cook.\n\n![Photo by dad](.attachments.9/b.jpg)\n"
    shelf = _Shelf(body)
    path = "Recipes/Chili.md"
    assert (await cookbook.set_cover(shelf, _who("dad"), path, ".attachments.9/b.jpg"))["detail"].get("forbidden")
    assert (await cookbook.set_cover(shelf, _who("mom"), path, ".attachments.9/elsewhere.jpg"))["status"] == "FAILURE"
    assert (await cookbook.set_cover(shelf, _who("mom"), path, ".attachments.9/b.jpg"))["status"] == "SUCCESS"
    assert cookbook.recipe_image(cookbook.strip_stored_header(shelf.text)) == ".attachments.9/b.jpg"

    # Removing the cover hands it to the next photo and deletes the file.
    assert (await cookbook.remove_photo(shelf, _who("mom"), path, ".attachments.9/b.jpg"))["status"] == "SUCCESS"
    left = cookbook.strip_stored_header(shelf.text)
    assert ".attachments.9/b.jpg" not in left and cookbook.recipe_image(left) == ".attachments.9/a.jpg"
    assert shelf.deleted == [".attachments.9/b.jpg"]
    assert (await cookbook.remove_photo(shelf, _who("mom"), path, ".attachments.9/a.jpg"))["status"] == "SUCCESS"
    assert cookbook.recipe_image(cookbook.strip_stored_header(shelf.text)) is None
