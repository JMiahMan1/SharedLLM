"""A parent answers ticked-off chores through Jarvis: "Jordan did the trash
right", "Taylor needs to redo the dishes", "what chores need checking?"
(docs/HEALTH_HUB.md, "Earning time and chores first")."""
import pytest

from services.execution.handlers import chores as handler
from services.execution.schemas import ChoreRequest

PARENTS = {"jordan": ["dad", "mom"], "taylor": ["dad", "mom"]}


@pytest.fixture
def approvals(monkeypatch):
    from services.common import notify
    from services.execution import main

    state = {
        "checks": [
            {"id": 1, "username": "jordan", "display_name": "Jordan", "day": "2026-11-04", "chore_id": "51", "title": "Trash", "status": "pending"},
            {"id": 2, "username": "jordan", "display_name": "Jordan", "day": "2026-11-04", "chore_id": "52", "title": "Dishes", "status": "pending"},
            {"id": 3, "username": "taylor", "display_name": "Taylor", "day": "2026-11-04", "chore_id": "60", "title": "Trash", "status": "pending"},
        ],
        "decided": [], "unticked": [], "sent": [], "parents_of": {"dad", "mom"},
    }

    async def identity(method, path, **kw):
        if method == "GET":
            viewer = kw["params"]["viewer"]
            seen = [c for c in state["checks"] if c["status"] == "pending" and viewer in state["parents_of"]]
            return 200, {"checks": seen}
        check_id = int(path.split("/")[-2])
        by, approve = kw["json"]["by"], kw["json"]["approve"]
        check = next(c for c in state["checks"] if c["id"] == check_id)
        if by not in PARENTS[check["username"]]:
            return 403, {"detail": "Only a parent can answer"}
        if check["status"] != "pending":
            return 409, {"detail": "Already answered"}
        check["status"] = "approved" if approve else "declined"
        state["decided"].append((check_id, by, approve))
        return 200, {"check": dict(check), "notify": [check["username"]], "untick": not approve,
                     "display_name": check["display_name"], "parents": PARENTS[check["username"]]}

    async def uncomplete(chore_id, user=None, x_internal_secret=None):
        state["unticked"].append((chore_id, user))
        return {"status": "SUCCESS"}

    async def send(payload, **kw):
        state["sent"].append(payload)
        return True

    monkeypatch.setattr(handler, "_identity", identity)
    monkeypatch.setattr(main, "uncomplete_skylight_chore", uncomplete)
    monkeypatch.setattr(notify, "send", send)
    return state


def ask(user, voice=False, **kw):
    ctx = {"user": user, "is_admin": False, "identified_by": "voice" if voice else None}
    return handler.handle_chores(ChoreRequest(user_context=ctx, **kw))


async def test_a_parent_hears_what_is_waiting(approvals):
    got = await ask("mom", action="checks")
    assert got.status == "SUCCESS" and got.message == "3 waiting for your OK: Jordan: Trash; Jordan: Dishes; Taylor: Trash."
    only = await ask("mom", action="checks", assignee="Taylor")
    assert only.message == "1 waiting for your OK: Taylor: Trash."
    approvals["parents_of"] = set()
    assert (await ask("jordan", action="checks")).message == "No chores are waiting for your OK."


async def test_approving_by_name_and_chore_tells_the_child(approvals):
    got = await ask("dad", action="approve", title="trash", assignee="Jordan")
    assert got.status == "SUCCESS" and got.message == "Approved Jordan's \"Trash\". The minutes count now."
    assert approvals["decided"] == [(1, "dad", True)] and approvals["unticked"] == []
    assert [(n["user"], n["title"]) for n in approvals["sent"]] == [("jordan", "Chore approved")]


async def test_a_redo_unticks_it_on_skylight(approvals):
    got = await ask("mom", action="redo", title="dishes")
    assert "back on the list" in got.message
    assert approvals["unticked"] == [("52", "jordan")]
    assert approvals["sent"][0]["user"] == "jordan" and "redo" in approvals["sent"][0]["body"]


async def test_two_matches_ask_which_unless_all(approvals):
    asked = await ask("dad", action="approve", title="trash")
    assert asked.status == "FAILURE" and "Which one, or all of them?" in asked.message
    assert approvals["decided"] == []
    both = await ask("dad", action="approve", title="trash", scope="all")
    assert both.status == "SUCCESS" and [d[0] for d in approvals["decided"]] == [1, 3]
    everything = await ask("mom", action="approve", assignee="jordan", scope="all")
    assert [d[0] for d in approvals["decided"]] == [1, 3, 2] and "Dishes" in everything.message


async def test_by_voice_alone_works_but_the_parents_are_told(approvals):
    got = await ask("mom", voice=True, action="approve", title="dishes")
    assert got.status == "SUCCESS"
    heard = [(n["user"], n["title"]) for n in approvals["sent"]]
    assert heard == [("jordan", "Chore approved"), ("dad", "Chore approved by voice"), ("mom", "Chore approved by voice")]
    assert "approved by voice as mom" in approvals["sent"][1]["body"]
    # Sending one back by voice needs no warning: it only costs the child.
    approvals["sent"].clear()
    await ask("mom", voice=True, action="redo", title="trash", assignee="taylor")
    assert [n["user"] for n in approvals["sent"]] == ["taylor"]


async def test_nothing_matching_or_waiting_says_so(approvals):
    missing = await ask("dad", action="approve", title="lawn")
    assert missing.status == "FAILURE" and "called \"lawn\"" in missing.message
    approvals["parents_of"] = set()
    assert "No ticked-off chores" in (await ask("jordan", action="approve", title="trash")).message


async def test_identity_refusing_is_passed_on(approvals):
    approvals["parents_of"] = {"dad", "mom", "aunt"}
    refused = await ask("aunt", action="approve", title="dishes")
    assert refused.status == "FAILURE" and "Only their parents" in refused.message


async def test_an_answer_given_in_the_app_meanwhile_is_not_doubled(approvals, monkeypatch):
    approvals["checks"][1]["status"] = "approved"  # answered in the app after the list was read

    async def stale_list(method, path, **kw):
        if method == "GET":
            return 200, {"checks": [dict(approvals["checks"][1], status="pending")]}
        return 409, {"detail": "Already answered"}

    monkeypatch.setattr(handler, "_identity", stale_list)
    assert (await ask("dad", action="approve", title="dishes")).message == "That was already answered."


async def test_any_familys_names_work_nothing_is_built_in(approvals):
    """Names come from identity (login and display name), so a family the code
    has never seen is matched the same way."""
    approvals["checks"] = [
        {"id": 9, "username": "zara.k", "display_name": "Zara Kowalski", "day": "2026-11-04", "chore_id": "77",
         "title": "Walk the dog", "status": "pending"},
    ]
    PARENTS["zara.k"] = ["pat"]
    approvals["parents_of"] = {"pat"}
    try:
        got = await ask("pat", action="approve", title="walk the dog", assignee="Zara")
        assert got.status == "SUCCESS" and "Zara Kowalski's \"Walk the dog\"" in got.message
        assert approvals["sent"][0]["user"] == "zara.k"
    finally:
        PARENTS.pop("zara.k")
