"""The family cookbook: everyone reads it, only the owner changes a recipe."""
import re

import pytest

from services.gateway import cookbook


class Store:
    """Each account's notes as {path: stored text}, behind the note actions."""

    def __init__(self):
        self.files: dict[str, dict[str, str]] = {"house": {}, "alex": {}, "sam": {}, "taylor": {}}
        self.deleted: list[str] = []

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
            self.deleted.append(payload["path"])
            return {"status": "SUCCESS"}
        if action == "move":
            # Like Nextcloud: the same file (same id, same attachments) under a new name.
            source = payload["path"]
            target = f"{source.rsplit('/', 1)[0]}/{re.sub(r'[^A-Za-z0-9._-]+', '-', payload['title']).strip('-')}.md"
            notes[target] = notes.pop(source)
            return {"status": "SUCCESS", "detail": {"path": target, "from": source}}
        raise AssertionError(action)


def caller(user, is_admin=False, as_house=False):
    return cookbook.Caller(
        user=user, is_admin=is_admin, house="house",
        house_creds={"user": "house"}, own_creds={"user": user}, as_house=as_house,
    )


@pytest.fixture
def store():
    s = Store()
    s.put("house", "Recipes/Chili.md", "Chili", "Tags: #dinner\nAdded by: sam\n\n## Ingredients\n- [ ] beans")
    s.put("house", "Recipes/Old-Soup.md", "Old Soup", "## Ingredients\n- [ ] water")
    s.put("house", "Recipes/Meal plan.md", "Meal plan", "#meal-plan")
    return s


@pytest.mark.asyncio
async def test_everyone_reads_the_same_shelf(store):
    for who in ("taylor", "sam"):
        listed = await cookbook.list_recipes(store.run, caller(who))
        assert sorted(n["title"] for n in listed["detail"]["notes"]) == ["Chili", "Old-Soup"]


@pytest.mark.asyncio
async def test_only_the_owner_changes_a_recipe(store):
    read = await cookbook.read_recipe(store.run, caller("taylor"), "Recipes/Chili.md")
    assert read["detail"]["owner"] == "sam" and not read["detail"]["can_change"]
    assert "Added by" not in read["message"]

    refused = await cookbook.save_recipe(store.run, caller("taylor"), "Chili", "nothing", "Recipes/Chili.md")
    assert refused["status"] == "FAILURE" and refused["detail"]["forbidden"]
    # An admin is not the owner either: they approve suggestions instead.
    refused = await cookbook.save_recipe(store.run, caller("alex", is_admin=True), "Chili", "x", "Recipes/Chili.md")
    assert refused["status"] == "FAILURE"
    gone = await cookbook.delete_recipe(store.run, caller("taylor"), "Recipes/Chili.md")
    assert gone["status"] == "FAILURE" and "Recipes/Chili.md" in store.files["house"]

    saved = await cookbook.save_recipe(
        store.run, caller("sam"), "Chili", "Tags: #dinner\nAdded by: taylor\n\n## Ingredients\n- [ ] more beans",
        "Recipes/Chili.md",
    )
    assert saved["status"] == "SUCCESS"
    stored = store.files["house"]["Recipes/Chili.md"]
    # The owner line is the server's, never the one the client sent.
    assert "Added by: sam" in stored and "taylor" not in stored and "more beans" in stored


@pytest.mark.asyncio
async def test_admin_recipes_are_changed_by_any_admin(store):
    assert (await cookbook.read_recipe(store.run, caller("taylor"), "Recipes/Old-Soup.md"))["detail"]["can_change"] is False
    saved = await cookbook.save_recipe(store.run, caller("alex", is_admin=True), "Old Soup", "## Ingredients\n- [ ] broth", "Recipes/Old-Soup.md")
    assert saved["status"] == "SUCCESS"
    assert "Added by: house" in store.files["house"]["Recipes/Old-Soup.md"]


@pytest.mark.asyncio
async def test_a_new_recipe_belongs_to_whoever_adds_it_and_never_overwrites(store):
    added = await cookbook.save_recipe(store.run, caller("taylor"), "Pancakes", "## Ingredients\n- [ ] flour")
    assert added["status"] == "SUCCESS"
    assert "Added by: taylor" in store.files["house"]["Recipes/Pancakes.md"]
    again = await cookbook.save_recipe(store.run, caller("sam"), "Pancakes", "mine")
    assert again["status"] == "FAILURE" and again["detail"]["exists"]
    assert "Added by: taylor" in store.files["house"]["Recipes/Pancakes.md"]

    house = await cookbook.save_recipe(store.run, caller("alex", is_admin=True, as_house=True), "Stew", "x")
    assert house["detail"]["owner"] == "house"


@pytest.mark.asyncio
async def test_a_suggestion_waits_for_the_owner(store):
    sent = await cookbook.suggest(store.run, caller("taylor"), "Recipes/Chili.md", "## Ingredients\n- [ ] corn", "add corn")
    assert sent["status"] == "SUCCESS"
    assert "beans" in store.files["house"]["Recipes/Chili.md"]

    # Someone who is neither the owner, an admin nor the sender does not see it.
    assert (await cookbook.list_suggestions(store.run, caller("other")))["detail"]["suggestions"] == []
    mine = (await cookbook.list_suggestions(store.run, caller("taylor")))["detail"]["suggestions"]
    assert len(mine) == 1 and mine[0]["mine"] and not mine[0]["can_review"]
    owned = (await cookbook.list_suggestions(store.run, caller("sam")))["detail"]["suggestions"]
    assert owned[0]["can_review"] and owned[0]["message"] == "add corn"

    refused = await cookbook.resolve_suggestion(store.run, caller("taylor"), owned[0]["path"], "approve")
    assert refused["status"] == "FAILURE"
    approved = await cookbook.resolve_suggestion(store.run, caller("sam"), owned[0]["path"], "approve")
    assert approved["status"] == "SUCCESS"
    stored = store.files["house"]["Recipes/Chili.md"]
    assert "corn" in stored and "Added by: sam" in stored
    assert owned[0]["path"] not in store.files["house"]


@pytest.mark.asyncio
async def test_an_admin_may_decide_and_the_sender_may_withdraw(store):
    await cookbook.suggest(store.run, caller("taylor"), "Recipes/Chili.md", "a", None)
    await cookbook.suggest(store.run, caller("taylor"), "Recipes/Chili.md", "b", None)
    pending = (await cookbook.list_suggestions(store.run, caller("alex", is_admin=True)))["detail"]["suggestions"]
    assert len(pending) == 2 and all(s["can_review"] for s in pending)
    rejected = await cookbook.resolve_suggestion(store.run, caller("alex", is_admin=True), pending[0]["path"], "reject")
    assert rejected["status"] == "SUCCESS"
    assert (await cookbook.resolve_suggestion(store.run, caller("sam"), pending[1]["path"], "withdraw"))["status"] == "FAILURE"
    assert (await cookbook.resolve_suggestion(store.run, caller("taylor"), pending[1]["path"], "withdraw"))["status"] == "SUCCESS"
    assert "beans" in store.files["house"]["Recipes/Chili.md"]


@pytest.mark.asyncio
async def test_paths_outside_the_cookbook_are_refused(store):
    for path in ("Notes/Diary.md", "Recipes/../Notes/Diary.md", "Recipes/Meal plan.md", "Recipes/Suggestions/x.md"):
        assert (await cookbook.read_recipe(store.run, caller("taylor"), path))["status"] == "FAILURE"
    assert (await cookbook.resolve_suggestion(store.run, caller("sam"), "Recipes/Chili.md", "approve"))["status"] == "FAILURE"


@pytest.mark.asyncio
async def test_moving_my_own_recipes_into_the_cookbook(store):
    store.put("taylor", "Recipes/Tacos.md", "Tacos", "## Ingredients\n- [ ] tortillas")
    store.put("taylor", "Recipes/Chili.md", "Chili", "my chili")
    store.put("taylor", "Recipes/Meal plan.md", "Meal plan", "#meal-plan")
    out = await cookbook.adopt(store.run, caller("taylor"))
    assert out["detail"]["moved"] == ["Tacos"]
    assert [k["title"] for k in out["detail"]["kept"]] == ["Chili"]
    assert "Added by: taylor" in store.files["house"]["Recipes/Tacos.md"]
    assert set(store.files["taylor"]) == {"Recipes/Chili.md", "Recipes/Meal plan.md"}


def test_owner_line_sits_after_the_header_lines():
    body = cookbook.with_owner("Tags: #x\nSource: a\n\n## Steps\n1. go", "taylor")
    assert body.split("\n")[:3] == ["Tags: #x", "Source: a", "Added by: taylor"]
    assert cookbook.recipe_owner(body, "house") == "taylor"
    assert cookbook.recipe_owner("## Steps\nAdded by: someone", "house") == "house"



@pytest.mark.asyncio
async def test_a_rename_moves_the_recipe_and_keeps_its_photos(store):
    store.put("house", "Recipes/5-tacos.md", "5-tacos",
              "Image: .attachments.42/photo.jpg\nAdded by: house\n\n## Ingredients\n- [ ] tortillas")
    current = await cookbook.read_recipe(store.run, caller("dad", is_admin=True), "Recipes/5-tacos.md")
    res = await cookbook.save_recipe(store.run, caller("dad", is_admin=True), "$5 Tacos", current["message"], "Recipes/5-tacos.md")
    assert res["status"] == "SUCCESS" and res["detail"]["path"] == "Recipes/5-Tacos.md"
    # Moved, never deleted: Nextcloud would take the attachment folder with a delete.
    assert store.deleted == [] and "Recipes/5-tacos.md" not in store.files["house"]
    saved = store.files["house"]["Recipes/5-Tacos.md"]
    assert saved.startswith("# $5 Tacos") and "Image: .attachments.42/photo.jpg" in saved


@pytest.mark.asyncio
async def test_a_rename_onto_another_recipe_is_refused(store):
    current = await cookbook.read_recipe(store.run, caller("sam"), "Recipes/Chili.md")
    res = await cookbook.save_recipe(store.run, caller("sam"), "Old Soup", current["message"], "Recipes/Chili.md")
    assert res["status"] == "FAILURE" and res["detail"].get("exists")
    assert "Recipes/Chili.md" in store.files["house"]


@pytest.mark.parametrize(("typed", "kept"), [
    ("$5 Tacos", "$5 Tacos"),
    ("Chili-Mac", "Chili-Mac"),
    ("50/50 Bars", "50/50 Bars"),
    ("Mom's Chili & Cornbread", "Mom's Chili & Cornbread"),
    ("crème brûlée", "Crème Brûlée"),
    ("mom's chili-mac with beans", "Mom's Chili-mac with Beans"),
    ("quick-beef-stew", "Quick Beef Stew"),
    ("  #1   Burger ", "#1 Burger"),
])
def test_a_name_keeps_every_character_it_was_typed_with(typed, kept):
    assert cookbook.tidy_title(typed) == kept


def test_names_match_the_way_their_files_do():
    assert cookbook.title_key("$5 Tacos") == cookbook.title_key("5-Tacos") == "5 tacos"
    assert cookbook.title_key("Crème Brûlée") == cookbook.title_key("creme-brulee")
    assert cookbook.display_title("$5 Tacos", "Recipes/5-Tacos.md") == "$5 Tacos"
    assert cookbook.display_title("Chili-Mac", "Recipes/Chili-Mac-and-Cheese.md") == "Chili-Mac"
    assert cookbook.display_title("5-tacos", "Recipes/5-tacos.md") == "5 Tacos"
    assert cookbook.display_title("Chili-Mac", "Recipes/Chili-Mac.md") == "Chili-Mac"


@pytest.mark.asyncio
async def test_a_name_with_symbols_is_saved_and_read_back_exactly(store):
    res = await cookbook.save_recipe(store.run, caller("sam"), "$5 Tacos", "## Ingredients\n- [ ] tortillas")
    path = res["detail"]["path"]
    back = await cookbook.read_recipe(store.run, caller("sam"), path)
    assert back["detail"]["title"] == "$5 Tacos"
    # "5 Tacos" would be the same file: refused, not overwritten.
    again = await cookbook.save_recipe(store.run, caller("sam"), "5 Tacos", "x")
    assert again["status"] == "FAILURE" and again["detail"].get("exists")
