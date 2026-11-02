"""The family cookbook: everyone reads it, only the owner changes a recipe."""
import re

import pytest

from services.gateway import cookbook


class Store:
    """Each account's notes as {path: stored text}, behind the note actions."""

    def __init__(self):
        self.files: dict[str, dict[str, str]] = {"house": {}, "jeremiah": {}, "michele": {}, "gracie": {}}

    def put(self, account, path, title, body):
        category = path.rsplit("/", 1)[0]
        self.files[account][path] = f"# {title}\nCategory: {category}\n\n{body}"

    async def run(self, creds, payload):
        notes = self.files[creds["user"]]
        action = payload["action"]
        if action == "list":
            base = payload["directories"][0] + "/"
            listed = [{"title": p.rsplit("/", 1)[-1][:-3], "path": p} for p in notes if p.startswith(base)]
            return {"status": "SUCCESS", "detail": {"notes": listed}}
        if action == "read":
            if payload["path"] not in notes:
                return {"status": "FAILURE", "message": "Note not found."}
            return {"status": "SUCCESS", "message": notes[payload["path"]], "detail": {"etag": "e1"}}
        if action == "write":
            path = payload.get("path") or f"{payload['category']}/{re.sub(r'[^A-Za-z0-9._-]+', '-', payload['title']).strip('-')}.md"
            if payload.get("if_none_match") and path in notes:
                return {"status": "FAILURE", "detail": {"conflict": True, "exists": True}}
            notes[path] = f"# {payload['title']}\nCategory: {payload['category']}\n\n{payload['content']}"
            return {"status": "SUCCESS", "detail": {"path": path}}
        if action == "delete":
            notes.pop(payload["path"], None)
            return {"status": "SUCCESS"}
        raise AssertionError(action)


def caller(user, is_admin=False, as_house=False):
    return cookbook.Caller(
        user=user, is_admin=is_admin, house="house",
        house_creds={"user": "house"}, own_creds={"user": user}, as_house=as_house,
    )


@pytest.fixture
def store():
    s = Store()
    s.put("house", "Recipes/Chili.md", "Chili", "Tags: #dinner\nAdded by: michele\n\n## Ingredients\n- [ ] beans")
    s.put("house", "Recipes/Old-Soup.md", "Old Soup", "## Ingredients\n- [ ] water")
    s.put("house", "Recipes/Meal plan.md", "Meal plan", "#meal-plan")
    return s


@pytest.mark.asyncio
async def test_everyone_reads_the_same_shelf(store):
    for who in ("gracie", "michele"):
        listed = await cookbook.list_recipes(store.run, caller(who))
        assert sorted(n["title"] for n in listed["detail"]["notes"]) == ["Chili", "Old-Soup"]


@pytest.mark.asyncio
async def test_only_the_owner_changes_a_recipe(store):
    read = await cookbook.read_recipe(store.run, caller("gracie"), "Recipes/Chili.md")
    assert read["detail"]["owner"] == "michele" and not read["detail"]["can_change"]
    assert "Added by" not in read["message"]

    refused = await cookbook.save_recipe(store.run, caller("gracie"), "Chili", "nothing", "Recipes/Chili.md")
    assert refused["status"] == "FAILURE" and refused["detail"]["forbidden"]
    # An admin is not the owner either: they approve suggestions instead.
    refused = await cookbook.save_recipe(store.run, caller("jeremiah", is_admin=True), "Chili", "x", "Recipes/Chili.md")
    assert refused["status"] == "FAILURE"
    gone = await cookbook.delete_recipe(store.run, caller("gracie"), "Recipes/Chili.md")
    assert gone["status"] == "FAILURE" and "Recipes/Chili.md" in store.files["house"]

    saved = await cookbook.save_recipe(
        store.run, caller("michele"), "Chili", "Tags: #dinner\nAdded by: gracie\n\n## Ingredients\n- [ ] more beans",
        "Recipes/Chili.md",
    )
    assert saved["status"] == "SUCCESS"
    stored = store.files["house"]["Recipes/Chili.md"]
    # The owner line is the server's, never the one the client sent.
    assert "Added by: michele" in stored and "gracie" not in stored and "more beans" in stored


@pytest.mark.asyncio
async def test_admin_recipes_are_changed_by_any_admin(store):
    assert (await cookbook.read_recipe(store.run, caller("gracie"), "Recipes/Old-Soup.md"))["detail"]["can_change"] is False
    saved = await cookbook.save_recipe(store.run, caller("jeremiah", is_admin=True), "Old Soup", "## Ingredients\n- [ ] broth", "Recipes/Old-Soup.md")
    assert saved["status"] == "SUCCESS"
    assert "Added by: house" in store.files["house"]["Recipes/Old-Soup.md"]


@pytest.mark.asyncio
async def test_a_new_recipe_belongs_to_whoever_adds_it_and_never_overwrites(store):
    added = await cookbook.save_recipe(store.run, caller("gracie"), "Pancakes", "## Ingredients\n- [ ] flour")
    assert added["status"] == "SUCCESS"
    assert "Added by: gracie" in store.files["house"]["Recipes/Pancakes.md"]
    again = await cookbook.save_recipe(store.run, caller("michele"), "Pancakes", "mine")
    assert again["status"] == "FAILURE" and again["detail"]["exists"]
    assert "Added by: gracie" in store.files["house"]["Recipes/Pancakes.md"]

    house = await cookbook.save_recipe(store.run, caller("jeremiah", is_admin=True, as_house=True), "Stew", "x")
    assert house["detail"]["owner"] == "house"


@pytest.mark.asyncio
async def test_a_suggestion_waits_for_the_owner(store):
    sent = await cookbook.suggest(store.run, caller("gracie"), "Recipes/Chili.md", "## Ingredients\n- [ ] corn", "add corn")
    assert sent["status"] == "SUCCESS"
    assert "beans" in store.files["house"]["Recipes/Chili.md"]

    # Someone who is neither the owner, an admin nor the sender does not see it.
    assert (await cookbook.list_suggestions(store.run, caller("other")))["detail"]["suggestions"] == []
    mine = (await cookbook.list_suggestions(store.run, caller("gracie")))["detail"]["suggestions"]
    assert len(mine) == 1 and mine[0]["mine"] and not mine[0]["can_review"]
    owned = (await cookbook.list_suggestions(store.run, caller("michele")))["detail"]["suggestions"]
    assert owned[0]["can_review"] and owned[0]["message"] == "add corn"

    refused = await cookbook.resolve_suggestion(store.run, caller("gracie"), owned[0]["path"], "approve")
    assert refused["status"] == "FAILURE"
    approved = await cookbook.resolve_suggestion(store.run, caller("michele"), owned[0]["path"], "approve")
    assert approved["status"] == "SUCCESS"
    stored = store.files["house"]["Recipes/Chili.md"]
    assert "corn" in stored and "Added by: michele" in stored
    assert owned[0]["path"] not in store.files["house"]


@pytest.mark.asyncio
async def test_an_admin_may_decide_and_the_sender_may_withdraw(store):
    await cookbook.suggest(store.run, caller("gracie"), "Recipes/Chili.md", "a", None)
    await cookbook.suggest(store.run, caller("gracie"), "Recipes/Chili.md", "b", None)
    pending = (await cookbook.list_suggestions(store.run, caller("jeremiah", is_admin=True)))["detail"]["suggestions"]
    assert len(pending) == 2 and all(s["can_review"] for s in pending)
    rejected = await cookbook.resolve_suggestion(store.run, caller("jeremiah", is_admin=True), pending[0]["path"], "reject")
    assert rejected["status"] == "SUCCESS"
    assert (await cookbook.resolve_suggestion(store.run, caller("michele"), pending[1]["path"], "withdraw"))["status"] == "FAILURE"
    assert (await cookbook.resolve_suggestion(store.run, caller("gracie"), pending[1]["path"], "withdraw"))["status"] == "SUCCESS"
    assert "beans" in store.files["house"]["Recipes/Chili.md"]


@pytest.mark.asyncio
async def test_paths_outside_the_cookbook_are_refused(store):
    for path in ("Notes/Diary.md", "Recipes/../Notes/Diary.md", "Recipes/Meal plan.md", "Recipes/Suggestions/x.md"):
        assert (await cookbook.read_recipe(store.run, caller("gracie"), path))["status"] == "FAILURE"
    assert (await cookbook.resolve_suggestion(store.run, caller("michele"), "Recipes/Chili.md", "approve"))["status"] == "FAILURE"


@pytest.mark.asyncio
async def test_moving_my_own_recipes_into_the_cookbook(store):
    store.put("gracie", "Recipes/Tacos.md", "Tacos", "## Ingredients\n- [ ] tortillas")
    store.put("gracie", "Recipes/Chili.md", "Chili", "my chili")
    store.put("gracie", "Recipes/Meal plan.md", "Meal plan", "#meal-plan")
    out = await cookbook.adopt(store.run, caller("gracie"))
    assert out["detail"]["moved"] == ["Tacos"]
    assert [k["title"] for k in out["detail"]["kept"]] == ["Chili"]
    assert "Added by: gracie" in store.files["house"]["Recipes/Tacos.md"]
    assert set(store.files["gracie"]) == {"Recipes/Chili.md", "Recipes/Meal plan.md"}


def test_owner_line_sits_after_the_header_lines():
    body = cookbook.with_owner("Tags: #x\nSource: a\n\n## Steps\n1. go", "gracie")
    assert body.split("\n")[:3] == ["Tags: #x", "Source: a", "Added by: gracie"]
    assert cookbook.recipe_owner(body, "house") == "gracie"
    assert cookbook.recipe_owner("## Steps\nAdded by: someone", "house") == "house"
