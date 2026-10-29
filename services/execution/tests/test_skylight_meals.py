"""Skylight Meals connector: Recipe Box, sittings and the grocery list.

All writes go through a fake `_skylight_request` that records what was sent --
the household's real Skylight is never written to. The read shapes are the ones
probed from the real account (2026-10-28): recipes carry `summary`/`description`,
sittings carry `attributes.instances` (no start field), lists side-load their
items under `included`.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from services.execution.main import (
    _skylight_recipe_description,
    _skylight_sitting_to_item,
    app,
)

client = TestClient(app)

SECRET = {"X-Internal-Secret": "test-secret"}


def _mock_session():
    return {
        "url": "https://app.ourskylight.com",
        "access_token": "mock-token",
        "frame_id": "3757473",
        "email": "test@example.com",
    }


async def _mock_get_session(*args, **kwargs):
    return _mock_session()


class _Fake:
    """A fake `_skylight_request` with canned per-endpoint answers."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, session, method, suffix, json_body=None, params=None):
        self.calls.append(
            {"method": method, "suffix": suffix, "body": json_body, "params": params}
        )
        handler = self.routes.get((method, suffix))
        if handler is None:
            return None
        return handler(json_body, params) if callable(handler) else handler

    def sent(self, method, suffix):
        return [c for c in self.calls if c["method"] == method and c["suffix"] == suffix]


DINNER = {"id": "cat-dinner", "type": "meal_category", "attributes": {"label": "Dinner", "color": "#c00"}}
LUNCH = {"id": "cat-lunch", "type": "meal_category", "attributes": {"label": "Lunch", "color": "#0c0"}}


def _categories(*rows):
    return {"data": list(rows)}


def _recipe(rid, summary, description="", category="cat-dinner"):
    return {
        "id": str(rid),
        "type": "meal_recipe",
        "attributes": {"summary": summary, "description": description},
        "relationships": {"meal_category": {"data": {"id": category, "type": "meal_category"}}},
    }


# --- pure helpers -----------------------------------------------------------

def test_recipe_description_builds_ingredients_and_instructions_blocks():
    body = (
        "# Pancakes\nCategory: Recipes\nTags: #level-2\n\n"
        "## Ingredients\n- [ ] 2 cups flour\n- [x] 1 tsp salt\n\n"
        "## Steps\n1. Mix everything.\n2. Cook.\n"
    )
    description = _skylight_recipe_description(body)
    assert description == (
        "Ingredients:\n- 2 cups flour\n- 1 tsp salt\n\n"
        "Instructions:\n1. Mix everything.\n2. Cook."
    )


def test_recipe_description_ignores_the_nutrition_and_notes_sections():
    body = (
        "## Ingredients\n- [ ] 2 cups flour\n\n## Steps\n1. Mix.\n\n"
        "## Nutrition\n*Per serving, as stated by the submitter - foodcom:* 350 kcal\n\n"
        "## Notes\n- Best with butter.\n"
    )
    description = _skylight_recipe_description(body)
    assert "350 kcal" not in description
    assert "butter" not in description
    assert description == "Ingredients:\n- 2 cups flour\n\nInstructions:\n1. Mix."


def test_recipe_description_without_headings_falls_back_to_bullets_and_numbers():
    description = _skylight_recipe_description("- Tortillas\n- Salsa\n1. Warm them.")
    assert description == "Ingredients:\n- Tortillas\n- Salsa\n\nInstructions:\n1. Warm them."


def test_a_sitting_takes_its_day_from_the_first_instance_and_never_invents_one():
    item = _skylight_sitting_to_item(
        {
            "id": "s1",
            "type": "meal_sitting",
            "attributes": {"summary": "", "instances": ["2026-10-31"], "recurring": True},
            "relationships": {
                "meal_category": {"data": {"id": "cat-dinner", "type": "meal_category"}},
                "meal_recipe": {"data": {"id": "88", "type": "meal_recipe"}},
            },
        }
    )
    assert item["date"] == "2026-10-31"
    assert item["meal_recipe_id"] == "88"
    assert item["meal_category_id"] == "cat-dinner"

    # `instances` lags for a recurring sitting: an empty list stays "unknown",
    # it must not become today.
    lagging = _skylight_sitting_to_item({"id": "s2", "type": "meal_sitting", "attributes": {}})
    assert lagging["date"] is None


# --- status -----------------------------------------------------------------

def test_meals_status_reports_not_configured_without_a_fake_success():
    async def _not_configured(*args, **kwargs):
        return False

    with patch("services.execution.main._skylight_configured", new=_not_configured):
        resp = client.get("/api/integrations/skylight/meals", headers=SECRET)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["configured"] is False
    assert data["categories"] == []


def test_meals_status_lists_categories_when_connected():
    fake = _Fake({("GET", "/meals/categories"): _categories(DINNER, LUNCH)})

    async def _configured(*args, **kwargs):
        return True

    with (
        patch("services.execution.main._skylight_configured", new=_configured),
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.get("/api/integrations/skylight/meals", headers=SECRET).json()
    assert data["configured"] is True
    assert [c["label"] for c in data["categories"]] == ["Dinner", "Lunch"]


# --- send a recipe ----------------------------------------------------------

def test_send_recipe_creates_it_in_the_recipe_box_under_dinner():
    fake = _Fake(
        {
            ("GET", "/meals/categories"): _categories(LUNCH, DINNER),
            ("GET", "/meals/recipes"): {"data": []},
            ("POST", "/meals/recipes"): {"data": _recipe(101, "Pancakes")},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/recipe",
            headers=SECRET,
            json={"title": "Pancakes", "body": "## Ingredients\n- [ ] flour\n\n## Steps\n1. Mix."},
        ).json()

    assert data["status"] == "SUCCESS"
    assert data["recipe_id"] == "101"
    created = fake.sent("POST", "/meals/recipes")
    assert len(created) == 1
    body = created[0]["body"]
    assert body["summary"] == "Pancakes"
    assert body["meal_category_id"] == "cat-dinner"  # Dinner wins over Lunch
    assert body["description"] == "Ingredients:\n- flour\n\nInstructions:\n1. Mix."


def test_send_recipe_updates_the_same_recipe_instead_of_duplicating():
    fake = _Fake(
        {
            ("GET", "/meals/categories"): _categories(DINNER),
            ("GET", "/meals/recipes"): {"data": [_recipe(77, "Pancakes", "old")]},
            ("PATCH", "/meals/recipes/77"): {"data": _recipe(77, "Pancakes", "new")},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/recipe",
            headers=SECRET,
            json={"title": "pancakes", "description": "new"},
        ).json()

    assert data["status"] == "SUCCESS"
    assert fake.sent("POST", "/meals/recipes") == []
    updated = fake.sent("PATCH", "/meals/recipes/77")
    assert len(updated) == 1
    assert updated[0]["body"]["description"] == "new"


def test_send_recipe_refuses_when_there_is_nothing_to_send():
    fake = _Fake({("GET", "/meals/categories"): _categories(DINNER)})
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/recipe",
            headers=SECRET,
            json={"title": "Empty", "body": "just prose, no lists"},
        ).json()
    assert data["status"] == "FAILURE"
    assert fake.sent("POST", "/meals/recipes") == []


def test_send_recipe_can_queue_the_grocery_list():
    fake = _Fake(
        {
            ("GET", "/meals/categories"): _categories(DINNER),
            ("GET", "/meals/recipes"): {"data": []},
            ("POST", "/meals/recipes"): {"data": _recipe(5, "Pancakes")},
            ("POST", "/meals/recipes/5/add_to_grocery_list"): {"data": _recipe(5, "Pancakes")},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/recipe",
            headers=SECRET,
            json={"title": "Pancakes", "description": "Ingredients:\n- flour", "add_to_grocery_list": True},
        ).json()
    assert data["status"] == "SUCCESS"
    assert data["grocery_list"] is True
    assert len(fake.sent("POST", "/meals/recipes/5/add_to_grocery_list")) == 1


# --- plan the week ----------------------------------------------------------

def test_plan_push_leaves_summary_blank_when_a_recipe_is_set():
    fake = _Fake(
        {
            ("GET", "/meals/categories"): _categories(DINNER),
            ("GET", "/meals/recipes"): {"data": []},
            ("POST", "/meals/recipes"): {"data": _recipe(9, "Chili")},
            ("POST", "/meals/sittings"): {
                "data": {
                    "id": "sit-1",
                    "type": "meal_sitting",
                    "attributes": {"instances": ["2026-10-31"]},
                }
            },
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/plan",
            headers=SECRET,
            json={"days": [{"date": "2026-10-31", "title": "Chili", "body": "## Ingredients\n- [ ] beans"}]},
        ).json()

    assert data["status"] == "SUCCESS"
    assert data["planned"][0]["sitting_id"] == "sit-1"
    sitting = fake.sent("POST", "/meals/sittings")[0]["body"]
    assert sitting["summary"] == ""  # the 422 rule: blank summary with a recipe
    assert sitting["meal_recipe_id"] == "9"
    assert sitting["meal_category_id"] == "cat-dinner"
    assert sitting["date"] == "2026-10-31"


def test_plan_push_collects_per_day_failures_and_keeps_the_rest():
    fake = _Fake(
        {
            ("GET", "/meals/categories"): _categories(DINNER),
            ("GET", "/meals/recipes"): {"data": []},
            ("POST", "/meals/recipes"): {"data": _recipe(9, "Chili")},
            ("POST", "/meals/sittings"): {"data": {"id": "sit-1", "attributes": {"instances": ["2026-10-31"]}}},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/plan",
            headers=SECRET,
            json={
                "days": [
                    {"date": "2026-10-31", "title": "Chili", "body": "## Ingredients\n- [ ] beans"},
                    {"date": "2026-11-01", "title": "Mystery", "body": "no lists here"},
                ]
            },
        ).json()

    assert data["status"] == "PARTIAL"
    assert [p["title"] for p in data["planned"]] == ["Chili"]
    assert data["failed"][0]["title"] == "Mystery"


# --- read the week ----------------------------------------------------------

def test_reading_the_week_never_turns_a_failure_into_an_empty_plan():
    fake = _Fake({})  # every endpoint answers None (a failed read)
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.get(
            "/api/integrations/skylight/meals/week",
            headers=SECRET,
            params={"date_min": "2026-10-26", "date_max": "2026-11-01"},
        ).json()
    assert data["status"] == "FAILURE"
    assert "sittings" not in data


def test_reading_the_week_returns_the_sittings():
    fake = _Fake(
        {
            ("GET", "/meals/sittings"): {
                "data": [
                    {
                        "id": "s1",
                        "type": "meal_sitting",
                        "attributes": {"summary": "", "instances": ["2026-10-31"]},
                        "relationships": {"meal_recipe": {"data": {"id": "88", "type": "meal_recipe"}}},
                    }
                ]
            }
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.get(
            "/api/integrations/skylight/meals/week",
            headers=SECRET,
            params={"date_min": "2026-10-26", "date_max": "2026-11-01"},
        ).json()
    assert data["status"] == "SUCCESS"
    assert data["sittings"][0]["date"] == "2026-10-31"
    assert data["sittings"][0]["meal_recipe_id"] == "88"
    assert fake.sent("GET", "/meals/sittings")[0]["params"] == {
        "date_min": "2026-10-26",
        "date_max": "2026-11-01",
    }


# --- grocery mirror ---------------------------------------------------------

def _list(lid, label, item_ids, default=False):
    return {
        "id": str(lid),
        "type": "list",
        "attributes": {"label": label, "kind": "to_do", "default_grocery_list": default},
        "relationships": {"list_items": {"data": [{"id": str(i), "type": "list_item"} for i in item_ids]}},
    }


def _item(iid, label):
    return {"id": str(iid), "type": "list_item", "attributes": {"label": label, "status": "pending"}}


def test_grocery_mirror_adds_missing_items_and_leaves_the_rest_alone():
    fake = _Fake(
        {
            ("GET", "/lists"): {
                "data": [_list(4, "Groceries", [1, 2], default=True)],
                "included": [_item(1, "2 lb chicken"), _item(2, "Rice")],
            },
            ("POST", "/lists/4/list_items"): {"data": _item(3, "Salsa")},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/grocery",
            headers=SECRET,
            json={"items": ["2 LB Chicken", "Salsa", "Beans"]},
        ).json()

    assert data["status"] == "SUCCESS"
    assert data["added"] == 2  # Salsa and Beans; "2 lb chicken" already there
    assert data["removed"] == 0
    labels = [c["body"]["label"] for c in fake.sent("POST", "/lists/4/list_items")]
    assert labels == ["Salsa", "Beans"]
    assert fake.sent("DELETE", "/lists/4/list_items/bulk_destroy") == []


def test_grocery_mirror_replace_bulk_destroys_what_dropped_off():
    fake = _Fake(
        {
            ("GET", "/lists"): {
                "data": [_list(4, "Groceries", [1, 2], default=True)],
                "included": [_item(1, "Chicken"), _item(2, "Old bread")],
            },
            ("POST", "/lists/4/list_items"): {"data": _item(3, "Salsa")},
            ("DELETE", "/lists/4/list_items/bulk_destroy"): {},
        }
    )
    with (
        patch("services.execution.main._get_skylight_session", new=_mock_get_session),
        patch("services.execution.main._skylight_request", new=fake),
    ):
        data = client.post(
            "/api/integrations/skylight/meals/grocery",
            headers=SECRET,
            json={"items": ["Chicken", "Salsa"], "replace": True},
        ).json()

    assert data["status"] == "SUCCESS"
    assert data["added"] == 1
    assert data["removed"] == 1
    doomed = fake.sent("DELETE", "/lists/4/list_items/bulk_destroy")[0]["body"]
    assert doomed == {"ids": ["2"]}


# --- the internal-secret guard ----------------------------------------------

def test_meals_routes_require_the_internal_secret():
    assert client.get("/api/integrations/skylight/meals").status_code == 403
    assert client.get("/api/integrations/skylight/meals/week").status_code == 403
    assert client.post("/api/integrations/skylight/meals/recipe", json={}).status_code == 403
    assert client.post("/api/integrations/skylight/meals/plan", json={}).status_code == 403
    assert client.post("/api/integrations/skylight/meals/grocery", json={}).status_code == 403
