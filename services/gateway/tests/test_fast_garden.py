"""Tier 1 garden commands: deterministic, grounded, and never guessing.

Every test drives ``fast_garden.try_handle`` against a fake Garden service, so
the assertions are about what a person would hear and which real call was made
-- not about regexes alone.
"""
from __future__ import annotations

import pytest

from services.gateway import fast_garden

CREDS = {"user": "noah", "family_role": "child", "is_admin": False}


class World:
    """A tiny Garden service: four plants, two rooms, and everything it was asked."""

    def __init__(self, *, outage: bool = False, extra_monstera: bool = False):
        self.outage = outage
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.plants = {
            "p-monty": {"plant_id": "p-monty", "nickname": "Monty", "common_name": "Monstera Deliciosa", "species_ref": "monstera_deliciosa", "category": "houseplant"},
            "p-fernie": {"plant_id": "p-fernie", "nickname": "Fernie", "common_name": "Boston Fern", "species_ref": "boston_fern", "category": "houseplant"},
            "p-basil": {"plant_id": "p-basil", "nickname": "Sir Basil", "common_name": "Sweet Basil", "species_ref": "basil_sweet", "category": "herb"},
            "p-tomato": {"plant_id": "p-tomato", "nickname": "Roma Row", "common_name": "Roma (Plum) Tomato", "species_ref": "tomato_roma", "category": "vegetable", "culinary_aliases": ["tomato"]},
        }
        if extra_monstera:
            self.plants["p-monty-two"] = {
                "plant_id": "p-monty-two", "nickname": "Monty Two", "common_name": "Monstera Deliciosa",
                "species_ref": "monstera_deliciosa", "category": "houseplant",
            }
        self.rooms = {
            "loc-living": {"location_id": "loc-living", "name": "Living Room", "kind": "raised_bed"},
            "loc-kitchen": {"location_id": "loc-kitchen", "name": "Kitchen", "kind": "raised_bed"},
        }

    async def __call__(self, method, path, *, creds=None, params=None, json=None):
        self.calls.append((method, path, params, json))
        if self.outage:
            raise RuntimeError("garden is down")
        if path == "/plants/resolve":
            return self._resolve((params or {}).get("q") or "")
        if path.startswith("/plants/") and path.endswith("/care"):
            # Relative, not a fixed date: "due in 7 days" must not drift with the clock.
            from datetime import UTC, datetime, timedelta

            return {
                "ok": True,
                "action": (json or {}).get("action"),
                "plant_id": path.split("/")[2],
                "next_due_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            }
        if path.endswith("/snooze"):
            return {"ok": True, "still_damp": True, "next_due_at": "2026-11-21T09:00:00+00:00"}
        if path.endswith("/harvest"):
            return {"ok": True, "plant_id": path.split("/")[2], "quantity": (json or {}).get("quantity")}
        if path.endswith("/water-all"):
            return {"ok": True, "location_name": "Living Room", "watered_count": 3, "watered": []}
        if path == "/today":
            return {
                "due_count": 2, "overdue_count": 1,
                "by_room": {"Living Room": [{"plant_id": "p-monty", "nickname": "Monty", "action": "water"}],
                            "Kitchen": [{"plant_id": "p-basil", "nickname": "Sir Basil", "action": "water"}]},
                "weather": {"rain_skipped": [], "frost_alerts": []},
            }
        if path == "/harvest/ready":
            return {"count": 1, "ready": [{"plant_id": "p-tomato", "nickname": "Roma Row", "common_name": "Roma (Plum) Tomato"}]}
        if path == "/pantry-batches":
            return {"count": 0, "batches": [], "goal_progress": [{"item": "Marinara sauce", "qty_put_up": 28, "target_qty": 40, "unit": "quarts", "percent_complete": 70}]}
        return {}

    def _resolve(self, query: str) -> dict:
        needle = query.strip().lower()
        hits = [
            plant for plant in self.plants.values()
            if needle in (plant.get("nickname") or "").lower()
            or needle in (plant.get("common_name") or "").lower()
            or needle in (plant.get("species_ref") or "").replace("_", " ")
        ]
        if not hits and needle.endswith("es"):
            singular = needle[:-2]
            hits = [p for p in self.plants.values() if singular in (p.get("common_name") or "").lower()]
        if hits:
            return {"query": query, "match_type": "plant" if len(hits) == 1 else "ambiguous", "plants": hits, "location": None}
        rooms = [room for room in self.rooms.values() if room["name"].lower() in needle or needle in room["name"].lower()]
        if rooms:
            return {"query": query, "match_type": "location", "plants": [], "location": rooms[0]}
        return {"query": query, "match_type": "none", "plants": [], "location": None}

    def posts(self) -> list[tuple[str, any]]:
        return [(path, body) for method, path, _params, body in self.calls if method == "POST"]


@pytest.fixture()
def world():
    return World()


# --------------------------------------------------------------------------- watering by name

async def _say(world, text: str, creds: dict | None = None):
    return await fast_garden.try_handle(text, creds or CREDS, call=world)


def test_i_watered_monty_logs_it_and_says_when_next():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "I watered Monty"))

    assert answer is not None and "Monty" in answer
    assert "watering due in 7 days" in answer
    assert world.posts() == [("/plants/p-monty/care", {"action": "water", "source": "voice"})]


def test_a_species_name_finds_the_plant():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "watered the monstera"))

    assert "Monty" in answer
    assert world.posts()[0][0] == "/plants/p-monty/care"


def test_marking_a_plant_watered_reads_naturally():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "mark Sir Basil watered"))

    assert "Sir Basil" in answer
    assert world.posts()[0][0] == "/plants/p-basil/care"


def test_fertilizing_uses_the_right_action():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "I fertilized Fernie"))

    assert "fertilizing" in answer
    assert world.posts()[0][1]["action"] == "fertilize"


def test_watering_a_whole_room_waters_the_room():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "I watered all the living room plants"))

    assert answer == "Watered 3 plants in Living Room."
    assert world.posts()[0][0] == "/locations/loc-living/water-all"


def test_a_room_phrase_like_kitchen_herbs_finds_the_room():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "watered the kitchen herbs"))

    assert "Kitchen" in answer
    assert world.posts()[0][0] == "/locations/loc-kitchen/water-all"


# --------------------------------------------------------------------------- snooze

def test_snooze_until_tomorrow_postpones_by_a_day():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "Snooze Fernie until tomorrow"))

    assert "Fernie" in answer
    method, path, params, _body = world.calls[-1]
    assert path == "/plants/p-fernie/snooze"
    assert params == {"still_damp": "true", "postpone_days": "1.0"}


def test_still_damp_is_a_snooze():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "Monty is still damp"))

    assert "Monty" in answer
    assert world.calls[-1][1] == "/plants/p-monty/snooze"


# --------------------------------------------------------------------------- summaries

def test_what_plants_need_water_today_names_them():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "What plants need water today?"))

    assert answer.startswith("2 plants need care today: Monty and Sir Basil.")
    assert "1 of them is overdue" in answer
    assert "1 ready to harvest" in answer
    assert "Marinara sauce is 28 of 40 quarts" in answer
    assert world.posts() == []


def test_any_garden_chores_is_the_same_answer():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "Any garden chores today?"))

    assert "need care today" in answer


# --------------------------------------------------------------------------- harvests

def test_logging_pounds_of_tomatoes_harvested():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "Log 3 pounds of tomatoes harvested"))

    assert "3 lb" in answer and "Roma Row" in answer
    path, body = world.posts()[0]
    assert path == "/plants/p-tomato/harvest"
    assert body["quantity"] == 3.0 and body["unit"] == "lb"


def test_picking_bunches_of_basil_keeps_the_unit():
    import asyncio

    world = World()
    answer = asyncio.run(_say(world, "I picked 2 bunches of basil"))

    assert "2 bunch" in answer
    assert world.posts()[0][1]["unit"] == "bunch"


# --------------------------------------------------------------------------- it must never guess

def test_an_ambiguous_name_asks_instead_of_watering_one():
    import asyncio

    world = World(extra_monstera=True)
    answer = asyncio.run(_say(world, "watered the monstera"))

    assert answer.startswith("Which plant did you mean:")
    assert "Monty" in answer and "Monty Two" in answer
    assert world.posts() == []


def test_an_unknown_plant_goes_back_to_the_chat_path():
    import asyncio

    world = World()

    # Even with the garden named, a name it does not know is the model's to answer.
    assert asyncio.run(_say(world, "I watered the plant Audrey")) is None
    assert world.posts() == []


def test_a_bare_unknown_name_is_left_to_jarvis():
    import asyncio

    world = World()

    # No garden word at all: this could be anything, so the model takes it.
    assert asyncio.run(_say(world, "I watered Audrey")) is None
    assert world.posts() == []


def test_an_ordinary_sentence_is_not_ours():
    import asyncio

    world = World()
    assert asyncio.run(_say(world, "turn off the kitchen lights")) is None
    assert asyncio.run(_say(world, "what is the weather like")) is None
    assert asyncio.run(_say(world, "write me a poem about rain")) is None
    assert world.calls == []


def test_a_garden_outage_is_admitted_not_guessed():
    import asyncio

    world = World(outage=True)
    answer = asyncio.run(_say(world, "I watered Monty"))

    assert answer == fast_garden.UNAVAILABLE


def test_watering_the_plants_alone_is_too_vague():
    import asyncio

    world = World()
    assert asyncio.run(_say(world, "water the plants")) is None
    assert world.calls == []


# --------------------------------------------------------------------------- it must not hijack

#: Sentences that belong to the model (or to another feature entirely).
NOT_OURS = (
    "when should I water Monty?",
    "how often should I water the monstera?",
    "do I need to water the plants today?",
    "add 2 pounds of chicken to my shopping list",
    "add 3 pounds of potatoes to the grocery list",
    "log 2 pounds of chicken in the recipe",
    "snooze the alarm for 10 minutes",
    "snooze the timer",
    "snooze my reminder until tomorrow",
    "is anything due for school?",
    "what is due for school tomorrow",
    "set a timer for 10 minutes",
    "turn off the kitchen lights",
    "write me a poem about rain",
)


@pytest.mark.parametrize("sentence", NOT_OURS)
def test_a_sentence_that_is_not_a_garden_command_falls_through(sentence):
    import asyncio

    world = World()

    assert asyncio.run(_say(world, sentence)) is None
    assert world.calls == []


def test_a_question_about_monty_never_logs_a_watering():
    import asyncio

    world = World()

    assert asyncio.run(_say(world, "When should I water Monty?")) is None
    assert world.posts() == []


def test_a_shopping_list_phrase_is_never_a_harvest():
    import asyncio

    world = World()

    assert asyncio.run(_say(world, "add 2 pounds of tomatoes to my shopping list")) is None
    assert world.posts() == []


def test_the_bare_verb_add_still_needs_a_garden_context():
    import asyncio

    world = World()

    assert asyncio.run(_say(world, "add 2 pounds of tomatoes")) is None
    # ... and with the garden named, it is a harvest again.
    assert asyncio.run(_say(world, "add 2 pounds of tomatoes from the garden")) is not None


def test_snoozing_a_device_is_not_snoozing_a_plant():
    import asyncio

    world = World()

    for sentence in ("snooze the alarm for 10 minutes", "snooze the timer", "snooze the reminder until tomorrow"):
        assert asyncio.run(_say(world, sentence)) is None
    assert world.posts() == []


def test_anything_due_is_only_ours_with_the_garden_named():
    import asyncio

    world = World()

    assert asyncio.run(_say(world, "is anything due for school?")) is None
    assert asyncio.run(_say(world, "is anything due in the garden?")) is not None


def test_an_unknown_name_always_falls_through():
    import asyncio

    world = World()

    assert asyncio.run(_say(world, "water Audrey")) is None
    assert asyncio.run(_say(world, "water the plant Audrey")) is None
    assert world.posts() == []
