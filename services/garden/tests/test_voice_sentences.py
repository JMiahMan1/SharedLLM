"""Voice fast path against the real Garden: what is ours, and what never is.

``services/gateway/fast_garden.try_handle`` is driven here against the Garden
service itself (a TestClient, a real database and the real seed), so a sentence
that slips past the parser still has to survive the Garden's own whole-phrase
name resolution. Every sentence in ``NOT_OURS`` must leave the garden untouched
and fall through to the chat path (``None``); every sentence in ``OURS`` must make
exactly the call it names.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from scripts.build_plants_seed import build_seed
from services.config import INTERNAL_SECRET
from services.garden.main import app
from services.garden.models import CareLog, HarvestLog, Plant
from services.gateway import fast_garden

PARENT = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "parent_one",
    "X-User-Role": "mother",
    "X-Family-Manage": "true",
}


@pytest.fixture()
def garden(tmp_path, monkeypatch):
    import services.garden.main as main_module

    seed = tmp_path / "plants-seed.db"
    build_seed(seed)
    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", str(seed))
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    client = TestClient(app)

    def room(name):
        resp = client.post("/locations", headers=PARENT, json={"name": name, "kind": "raised_bed"})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    bedroom = room("Bedroom")
    living = room("Living Room")
    room("Kitchen")
    for nickname, species, category, where in (
        ("Monty", "monstera_deliciosa", "houseplant", living),
        ("Fernie", "nephrolepis_exaltata", "houseplant", bedroom),
        ("Sir Basil", "basil_sweet", "herb", None),
        ("Roma Row", "tomato_roma", "vegetable", None),
        ("Snapdragon", "spathiphyllum_wallisii", "houseplant", bedroom),
        ("Calla Lily", "lilium", "houseplant", bedroom),
    ):
        body = {"nickname": nickname, "species_ref": species, "category": category}
        if where:
            body["location_id"] = where
        assert client.post("/plants", headers=PARENT, json=body).status_code == 201

    calls: list[tuple[str, str]] = []

    async def call(method, path, *, creds=None, params=None, json=None):
        calls.append((method, path))
        resp = client.request(method, path, params=params, json=json, headers=PARENT)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return {"__error__": str(body.get("detail")), "__status__": resp.status_code}
        return body

    client.calls = calls
    client.say = lambda text: asyncio.run(fast_garden.try_handle(text, {"user": "parent_one"}, call=call))
    with Session(engine) as db:
        client.db = db
        yield client


def _writes(garden) -> list[str]:
    return [path for method, path in garden.calls if method != "GET"]


def _care_rows(garden) -> list[CareLog]:
    garden.db.expire_all()
    return list(garden.db.exec(select(CareLog).where(CareLog.action == "water")).all())


#: Sentences the review found being hijacked, plus their near relatives.
NOT_OURS = (
    "remind me to water monty tomorrow",
    "remind me to water Monty",
    "don't water monty",
    "do not water Monty today",
    "I didn't water Monty",
    "never water Fernie",
    "set a reminder to water the plants in the bedroom at 6",
    "water the plants in the bedroom at 6",
    "water Monty tomorrow",
    "water Fernie every Sunday",
    "water the bedroom plants tonight",
    "I watered Monty yesterday",
    "snooze for 10 minutes",
    "snooze Monty for 10 minutes",
    "snooze the alarm for 10 minutes",
    "my hair is still wet",
    "the paint is still wet",
    "add 3 bunches of herbs to the list",
    "add 3 bunches of basil to the list",
    "add 2 pounds of tomatoes",
    "log 2 pounds of chicken in the recipe",
    "which herbs need full sun",
    "which herbs need full sun?",
    "what plants need repotting in spring?",
    "when should I water Monty?",
    "how often should I water the monstera?",
    "is the outdoor light on?",
    "lock the outdoor door",
    "turn off the kitchen lights",
    "water Audrey",
    "I watered the plant Audrey",
    "I watered the bedroom at 6",
)


@pytest.mark.parametrize("sentence", NOT_OURS)
def test_a_sentence_that_is_not_a_garden_report_falls_through(garden, sentence):
    assert garden.say(sentence) is None
    assert _writes(garden) == []
    assert _care_rows(garden) == []


#: (sentence, the one write it must make)
OURS = (
    ("I watered Monty", "/plants/{Monty}/care"),
    ("watered Monty this morning", "/plants/{Monty}/care"),
    ("okay I just watered Fernie", "/plants/{Fernie}/care"),
    ("mark Sir Basil watered", "/plants/{Sir Basil}/care"),
    ("I fertilized Fernie", "/plants/{Fernie}/care"),
    ("I watered the snapdragon", "/plants/{Snapdragon}/care"),
    ("watered the calla lily", "/plants/{Calla Lily}/care"),
    ("I watered all the bedroom plants", "/locations/{Bedroom}/water-all"),
    ("watered the plants in the bedroom", "/locations/{Bedroom}/water-all"),
    ("snooze Fernie until tomorrow", "/plants/{Fernie}/snooze"),
    ("snooze Fernie for 2 days", "/plants/{Fernie}/snooze"),
    ("Monty is still damp", "/plants/{Monty}/snooze"),
    ("log 3 pounds of tomatoes harvested", "/plants/{Roma Row}/harvest"),
    ("I picked 2 bunches of basil", "/plants/{Sir Basil}/harvest"),
    ("add 2 pounds of tomatoes from the garden", "/plants/{Roma Row}/harvest"),
)


def _ids(garden) -> dict[str, str]:
    garden.db.expire_all()
    ids = {row.nickname: row.id for row in garden.db.exec(select(Plant)).all()}
    for room in garden.get("/locations", headers=PARENT).json():
        ids[room["name"]] = room["id"]
    return ids


@pytest.mark.parametrize(("sentence", "write"), OURS)
def test_a_garden_report_makes_exactly_its_call(garden, sentence, write):
    expected = write.format_map(_ids(garden))

    answer = garden.say(sentence)

    assert answer, sentence
    assert _writes(garden) == [expected]


#: Questions about the day: answered with the summary, never logging anything.
SUMMARIES = (
    "what plants need water today?",
    "what needs watering?",
    "any garden chores today?",
    "is anything due in the garden?",
    "do the plants need water today?",
    "garden status",
)


@pytest.mark.parametrize("sentence", SUMMARIES)
def test_a_care_question_gets_the_summary(garden, sentence):
    answer = garden.say(sentence)

    assert answer and ("need care today" in answer or "Nothing in the garden" in answer)
    assert _writes(garden) == []


def test_a_bedroom_reminder_never_waters_the_bedroom(garden):
    """The exact sentence that watered every Bedroom plant."""
    garden.say("set a reminder to water the plants in the bedroom at 6")

    assert _care_rows(garden) == []
    assert garden.db.exec(select(HarvestLog)).all() == []


def test_whole_phrase_resolution_needs_every_word(garden):
    """The Garden side: a longer phrase never reaches a name inside it."""
    def resolve(q):
        return garden.get("/plants/resolve", headers=PARENT, params={"q": q, "whole": "true"}).json()

    assert resolve("plants in the bedroom at 6")["match_type"] == "none"
    assert resolve("hair")["match_type"] == "none"
    assert resolve("bedroom")["match_type"] == "location"
    assert resolve("tomatoes")["match_type"] == "plant"
    assert resolve("monstera")["match_type"] == "plant"
    # The typed (non-voice) resolver keeps its looser phrase matching.
    assert garden.get("/plants/resolve", headers=PARENT, params={"q": "kitchen herbs"}).json()["match_type"] == "location"


def test_an_unreachable_garden_is_said_not_raised():
    import aiohttp

    async def down(*_args, **_kwargs):
        raise aiohttp.ClientConnectionError("connection refused")

    for sentence in ("I watered Monty", "what needs watering?", "log 3 pounds of tomatoes harvested"):
        answer = asyncio.run(fast_garden.try_handle(sentence, {"user": "parent_one"}, call=down))
        assert answer and ("unavailable" in answer.lower() or "couldn't reach" in answer)


def test_a_failed_read_is_not_nothing_needs_care():
    async def broken(method, path, **_kwargs):
        if path == "/today":
            return {"__error__": "database is locked", "__status__": 500}
        return {}

    answer = asyncio.run(fast_garden.try_handle("what needs watering?", {"user": "parent_one"}, call=broken))

    assert answer and "Nothing" not in answer
    assert "database is locked" in answer
