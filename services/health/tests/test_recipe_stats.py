"""Family cookbook stars and times made."""

PATH = "Recipes/Chicken-Chili.md"


def test_stars_average_across_the_family_and_mine_is_my_own(client):
    assert client.put("/recipes/rating", json={"recipe": PATH, "stars": 5}).json()["mine"] == 5
    other = client.put("/recipes/rating", json={"recipe": PATH, "stars": 3}, headers={"X-User-Id": "mom"}).json()
    assert other == {**other, "rating": 4.0, "ratings": 2, "mine": 3}
    mine = client.get("/recipes/stats").json()["recipes"][PATH]
    assert mine["mine"] == 5 and mine["rating"] == 4.0
    cleared = client.put("/recipes/rating", json={"recipe": PATH, "stars": 0}).json()
    assert cleared["mine"] is None and cleared["ratings"] == 1


def test_made_counts_once_a_day_per_person(client):
    client.post("/recipes/made", json={"recipe": PATH, "day": "2026-10-30"})
    client.post("/recipes/made", json={"recipe": PATH, "day": "2026-10-30"})
    client.post("/recipes/made", json={"recipe": PATH, "day": "2026-10-31"}, headers={"X-User-Id": "mom"})
    stat = client.get("/recipes/stats").json()["recipes"][PATH]
    assert stat["made"] == 2 and stat["made_by_me"] == 1 and stat["last_made"] == "2026-10-31"
    undone = client.delete("/recipes/made", params={"recipe": PATH, "day": "2026-10-30"}).json()
    assert undone["made"] == 1


def test_a_rename_carries_the_stats(client):
    client.put("/recipes/rating", json={"recipe": PATH, "stars": 4})
    client.post("/recipes/made", json={"recipe": PATH})
    client.post("/recipes/rename", json={"old": PATH, "new": "Recipes/White-Chili.md"})
    stats = client.get("/recipes/stats").json()["recipes"]
    assert PATH not in stats and stats["Recipes/White-Chili.md"]["made"] == 1


def test_only_cookbook_paths(client):
    for bad in ("Notes/Diary.md", "Recipes/../x.md", "Recipes/a/b.md"):
        assert client.put("/recipes/rating", json={"recipe": bad, "stars": 3}).status_code == 422
