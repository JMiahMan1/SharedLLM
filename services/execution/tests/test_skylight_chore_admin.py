"""Adding, changing and removing Skylight chores (docs/ACHIEVEMENTS.md, "Managing chores")."""
from datetime import date

import pytest

from services.execution import skylight_chores as sc
from services.execution.handlers import chores as handler
from services.execution.schemas import ChoreRequest

TODAY = date(2026, 11, 3)  # a Tuesday
MEMBERS = [{"id": "11", "label": "Noah", "color": "#f00"}, {"id": "12", "label": "Gracie", "color": None},
           {"id": "13", "label": "Jeremiah", "color": None}]


# --- pure helpers ----------------------------------------------------------


def test_repeat_words_and_rules():
    assert sc.repeat_rule(None) is None and sc.repeat_rule("once") is None
    assert sc.repeat_rule("daily") == "RRULE:FREQ=DAILY"
    assert sc.repeat_rule("Every weekday") == "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert sc.repeat_rule("weekly", TODAY) == "RRULE:FREQ=WEEKLY;BYDAY=TU"
    assert sc.repeat_rule("fri, mon and wednesday") == "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert sc.repeat_rule("every saturday") == "RRULE:FREQ=WEEKLY;BYDAY=SA"
    assert sc.repeat_rule("FREQ=WEEKLY;BYDAY=MO") == "RRULE:FREQ=WEEKLY;BYDAY=MO"
    for bad in ("sometimes", "mon and pizza", "RRULE:nonsense"):
        with pytest.raises(sc.ChoreError):
            sc.repeat_rule(bad)


def test_times_and_days():
    assert sc.clock("7pm") == "19:00" and sc.clock("7:30 a.m.") == "07:30" and sc.clock("12am") == "00:00"
    assert sc.clock("19:05") == "19:05" and sc.clock("") is None
    for bad in ("25:00", "13pm", "noonish"):
        with pytest.raises(sc.ChoreError):
            sc.clock(bad)
    assert sc.day(None, TODAY) == TODAY and sc.day("tomorrow", TODAY) == date(2026, 11, 4)
    assert sc.day("2026-12-01", TODAY) == date(2026, 12, 1)
    with pytest.raises(sc.ChoreError):
        sc.day("next blue moon", TODAY)


def test_members_skip_calendar_only_categories():
    raw = {"data": [
        {"id": 11, "attributes": {"label": "Noah", "color": "#f00", "selected_for_chore_chart": True}},
        {"id": 20, "attributes": {"label": "Holidays", "selected_for_chore_chart": False, "linked_to_profile": False}},
        {"id": 21, "attributes": {"label": ""}},
    ]}
    assert sc.members_from(raw) == [{"id": "11", "label": "Noah", "color": "#f00"}]


def test_member_names_match_logins_and_unknown_names_are_named():
    assert sc.member_ids(["noah", "GRACIE"], MEMBERS) == ["11", "12"]
    assert sc.member_ids(["jeremiahs"], MEMBERS) == ["13"]  # a login containing the label
    with pytest.raises(sc.ChoreError, match="No one on the Skylight chore chart is called Zed"):
        sc.member_ids(["Zed"], MEMBERS)


def test_a_one_off_is_one_post_per_person_and_a_repeat_is_one_create_multiple():
    one = sc.create_requests({"title": " Feed  the dog ", "assignees": ["noah", "gracie"], "time": "7am",
                              "stars": 2, "emoji": "🐶"}, MEMBERS, TODAY)
    assert one == [
        ("/chores", {"summary": "Feed the dog", "start": "2026-11-03", "start_time": "07:00", "reward_points": 2,
                     "emoji_icon": "🐶", "category_id": "11"}),
        ("/chores", {"summary": "Feed the dog", "start": "2026-11-03", "start_time": "07:00", "reward_points": 2,
                     "emoji_icon": "🐶", "category_id": "12"}),
    ]
    (path, body), = sc.create_requests({"title": "Hay", "assignees": ["noah", "gracie"], "repeat": "weekdays",
                                        "date": "tomorrow", "until": "2026-12-20"}, MEMBERS, TODAY)
    assert path == "/chores/create_multiple"
    assert body == {"summary": "Hay", "start": "2026-11-04", "category_ids": ["11", "12"],
                    "recurrence_set": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"], "recurring_until": "2026-12-20"}
    for bad, msg in (({"assignees": ["noah"]}, "needs a name"), ({"title": "x"}, "Who is the chore for"),
                     ({"title": "x", "assignees": ["noah"], "stars": 500}, "between 0 and 100"),
                     ({"title": "x", "assignees": ["noah"], "repeat": "daily", "until": "2026-01-01"}, "stops before")):
        with pytest.raises(sc.ChoreError, match=msg):
            sc.create_requests(bad, MEMBERS, TODAY)


def test_update_sets_only_what_was_asked():
    assert sc.update_body({"title": "Hay barn", "apply_to": "all"}, MEMBERS) == {"summary": "Hay barn", "apply_to": "all"}
    assert sc.update_body({"assignees": ["gracie"], "time": "6pm", "repeat": "none"}, MEMBERS, TODAY) == {
        "category_id": "12", "start_time": "18:00", "recurrence_set": []}
    with pytest.raises(sc.ChoreError, match="Nothing to change"):
        sc.update_body({}, MEMBERS)
    with pytest.raises(sc.ChoreError, match="one person at a time"):
        sc.update_body({"assignees": ["noah", "gracie"]}, MEMBERS)


# --- calls against a fake Skylight -----------------------------------------


class FakeSkylight:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    async def __call__(self, session, method, suffix, json_body=None, params=None):
        self.calls.append((method, suffix, json_body, params))
        if (method, suffix) in self.fail:
            return None
        if suffix == "/categories":
            return {"data": [{"id": m["id"], "attributes": {"label": m["label"]}} for m in MEMBERS]}
        if method == "POST":
            return {"data": {"id": "900", "attributes": json_body}}
        return {}


async def test_create_update_delete_send_the_documented_calls():
    sky = FakeSkylight()
    made = await sc.create({}, sky, {"title": "Trash", "assignees": ["noah"]})
    assert len(made) == 1 and sky.calls[0][:2] == ("GET", "/categories")
    assert sky.calls[1][:2] == ("POST", "/chores") and sky.calls[1][2]["category_id"] == "11"

    await sc.update({}, sky, "900-2026-11-03", {"title": "Trash out", "apply_to": "one"})
    assert sky.calls[-1] == ("PUT", "/chores/900-2026-11-03", {"summary": "Trash out", "apply_to": "one"}, None)

    await sc.delete({}, sky, "900", "all")
    assert sky.calls[-1] == ("DELETE", "/chores/900", None, {"apply_to": "all"})
    await sc.delete({}, sky, "901")
    assert sky.calls[-1] == ("DELETE", "/chores/901", None, None)
    for bad in ("../frames", "9 00", ""):
        with pytest.raises(sc.ChoreError, match="Not a chore id"):
            await sc.delete({}, sky, bad)


async def test_failures_say_what_happened():
    with pytest.raises(sc.ChoreError, match="did not accept"):
        await sc.create({}, FakeSkylight(fail={("POST", "/chores")}), {"title": "T", "assignees": ["noah"]})
    with pytest.raises(sc.ChoreError, match="chore chart"):
        await sc.create({}, FakeSkylight(fail={("GET", "/categories")}), {"title": "T", "assignees": ["noah"]})
    with pytest.raises(sc.ChoreError, match="did not remove"):
        await sc.delete({}, FakeSkylight(fail={("DELETE", "/chores/5")}), "5")


# --- the assistant's handler: who may do what -------------------------------


@pytest.fixture
def board(monkeypatch):
    """Today's chores, a fake Skylight, and each caller's family role."""
    from services.execution import main

    sky = FakeSkylight()
    state = {"roles": {"dad": "father", "mom": "mother", "noah": "child"}, "completed": []}
    chores = [
        {"id": "50-2026-11-03", "title": "Feed the chickens", "assignees": ["Noah"], "completed": False,
         "recurrence": "RRULE:FREQ=DAILY", "start_time": "07:00"},
        {"id": "51-2026-11-03", "title": "Trash", "assignees": ["Gracie"], "completed": True, "recurrence": None},
        {"id": "52-2026-11-03", "title": "Trash and recycling", "assignees": ["Noah"], "completed": False,
         "recurrence": None},
    ]

    async def role(user):
        return state["roles"].get(user, "other")

    async def session(user=None):
        return {"frame_id": "f"}

    async def get_chores(user=None, date=None, x_internal_secret=None):
        mine = [c for c in chores if not user or any(sc.same_person(user, a) for a in c["assignees"])]
        return {"status": "SUCCESS", "chores": mine}

    async def complete(chore_id, user=None, x_internal_secret=None):
        state["completed"].append((chore_id, user))
        return {"status": "SUCCESS"}

    monkeypatch.setattr(handler, "family_role", role)
    monkeypatch.setattr(main, "_get_skylight_session", session)
    monkeypatch.setattr(main, "_skylight_request", sky)
    monkeypatch.setattr(main, "get_skylight_chores", get_chores)
    monkeypatch.setattr(main, "complete_skylight_chore", complete)
    state["sky"] = sky
    return state


def ask(user, voice=False, **kw):
    ctx = {"user": user, "is_admin": False, "identified_by": "voice" if voice else None}
    return handler.handle_chores(ChoreRequest(user_context=ctx, **kw))


async def test_parents_add_kids_cannot(board):
    done = await ask("mom", action="add", title="Fold laundry", assignee="noah", repeat="weekends", time="10am")
    assert done.status == "SUCCESS" and "Fold laundry" in done.message and "weekends" in done.message
    assert board["sky"].calls[-1][1] == "/chores/create_multiple"
    by_voice = await ask("dad", voice=True, action="add", title="Hay", assignee="gracie")
    assert by_voice.status == "SUCCESS"  # adding a chore is never an unlock
    refused = await ask("noah", action="add", title="Nothing", assignee="noah")
    assert refused.status == "FAILURE" and "Only a parent" in refused.message


async def test_removing_needs_a_parent_not_just_their_voice(board):
    assert "voice alone" in (await ask("dad", voice=True, action="remove", title="Feed the chickens")).message
    assert "Only a parent" in (await ask("noah", action="remove", title="Feed the chickens")).message
    gone = await ask("dad", action="remove", title="feed the chickens")
    assert gone.status == "SUCCESS" and "for that day" in gone.message
    assert board["sky"].calls[-1] == ("DELETE", "/chores/50-2026-11-03", None, {"apply_to": "one"})
    every = await ask("mom", action="remove", title="Feed the chickens", scope="all")
    assert "every day of it" in every.message and board["sky"].calls[-1][3] == {"apply_to": "all"}


async def test_an_exact_title_wins_and_ambiguity_is_asked_about(board):
    await ask("dad", action="remove", title="Trash", assignee="gracie")
    assert board["sky"].calls[-1] == ("DELETE", "/chores/51-2026-11-03", None, None)  # one-off: no apply_to
    unsure = await ask("dad", action="remove", title="trash and")  # matches both trash chores
    assert unsure.status == "FAILURE" and "Which one?" in unsure.message and "Trash and recycling" in unsure.message
    missing = await ask("dad", action="remove", title="Mow the moon")
    assert missing.status == "FAILURE" and "couldn't find" in missing.message


async def test_kids_list_and_tick_off_only_their_own(board):
    mine = await ask("noah", action="list")
    assert mine.status == "SUCCESS" and "2 of 2 left" in mine.message and "Gracie" not in mine.message
    assert "only see your own" in (await ask("noah", action="list", assignee="gracie")).message
    everyone = await ask("mom", action="list")
    assert "2 of 3 left" in everyone.message

    assert (await ask("noah", voice=True, action="complete", title="feed the chickens")).status == "SUCCESS"
    assert board["completed"] == [("50-2026-11-03", "noah")]
    assert "only tick off your own" in (await ask("noah", action="complete", title="Trash", assignee="gracie")).message
    assert "only tick off your own" in (
        await ask("dad", voice=True, action="complete", title="Trash", assignee="gracie")).message
    assert (await ask("dad", action="complete", title="Trash", assignee="gracie")).status == "SUCCESS"
