"""Edits and reactions on private chat messages: sealed, members only, the author alone edits.

The server keeps a sealed edit and sealed reactions per message. These tests
check it refuses anything readable, lets only the two members react or mark
seen, lets only the parent Talk says wrote a message edit it, keeps "seen"
per member, forgets a deleted message's marks, and puts no emoji or text in
the notice it sends.
"""
import base64
import json

import pytest
from fastapi.responses import JSONResponse

from services.gateway import main as gateway_main
from services.gateway import private_chat, private_chat_marks
from services.gateway.tests.test_private_chat import _as, _create, _legacy, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
BASE = f"/api/private-chat/chats/{TOKEN}"

#: Talk's record of who sent each message in the room.
AUTHORS = {7: "alex", 8: "sam"}


def _sealed(n: int = 40) -> str:
    """A sealed record the way a device makes one: the locked line and the envelope."""
    iv = base64.b64encode(bytes(range(12))).decode()
    ct = base64.b64encode(bytes([9]) * n).decode()
    envelope = json.dumps({"kind": "sealed", "title": "Encrypted message", "v": 1, "iv": iv, "ct": ct})
    return f"\U0001f512 Encrypted message\n\n```jarvis-envelope\n{envelope}\n```"


@pytest.fixture
def chat(env, monkeypatch):  # noqa: F811
    """A chat still on Talk: Talk's record says who wrote each message (a moved chat is tested in test_private_chat_messages)."""
    _legacy(env)
    talk = private_chat._talk

    async def with_messages(request, payload):
        if payload.get("action") == "message":
            env["calls"].append(payload)
            mid = payload.get("message_id")
            if mid not in AUTHORS:
                return 200, {"status": "FAILURE", "message": "No such message."}
            return 200, {"status": "SUCCESS", "detail": {"message_record": {
                "id": mid, "actor_type": "users", "actor_id": AUTHORS[mid].upper(), "deleted": False,
                "system_message": "", "message": _sealed()}}}
        return await talk(request, payload)

    monkeypatch.setattr(private_chat, "_talk", with_messages)
    return env


def _marks(ctx, user: str) -> list[dict]:
    resp = ctx["client"].get(f"{BASE}/marks", headers=_as(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["marks"]


def _react(ctx, user: str, message_id: int = 8, sealed: str | None = None):
    return ctx["client"].post(f"{BASE}/messages/{message_id}/reactions", json={"sealed": sealed or _sealed()}, headers=_as(user))


# --- Who may --------------------------------------------------------------------


@pytest.mark.parametrize("outsider", ["kid", "teen", "gran", "uncle", "nochat"])
def test_only_the_two_members_react_edit_list_or_mark_seen(chat, outsider):
    c = chat["client"]
    assert _react(chat, outsider).status_code in (403, 404)
    assert c.put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as(outsider)).status_code in (403, 404)
    assert c.get(f"{BASE}/marks", headers=_as(outsider)).status_code in (403, 404)
    assert c.post(f"{BASE}/marks/seen", json={"ids": []}, headers=_as(outsider)).status_code in (403, 404)
    assert not (chat["root"] / "marks").exists()


def test_either_parent_reacts_to_either_parents_message(chat):
    assert _react(chat, "alex", 8).status_code == 200
    assert _react(chat, "sam", 8).status_code == 200
    assert _react(chat, "sam", 7).status_code == 200
    marks = {m["message_id"]: m for m in _marks(chat, "alex")}
    assert [r["by"] for r in marks[8]["reactions"]] == ["alex", "sam"]
    assert [r["by_name"] for r in marks[7]["reactions"]] == ["Sam"]


def test_a_reaction_on_a_message_talk_does_not_have_is_refused(chat):
    assert _react(chat, "alex", 99).status_code == 404
    assert _react(chat, "alex", "nope").status_code == 404


def test_only_the_author_edits_as_talk_records_it(chat):
    c = chat["client"]
    # Message 7 is Alex's (Talk says so, whatever the case of its actor id).
    assert c.put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as("sam")).status_code == 403
    resp = c.put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as("alex"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["edit"]["own"] is True
    assert c.put(f"{BASE}/messages/8/edit", json={"sealed": _sealed()}, headers=_as("alex")).status_code == 403
    marks = _marks(chat, "sam")
    assert len(marks) == 1 and marks[0]["message_id"] == 7
    assert marks[0]["edit"]["by"] == "alex" and marks[0]["edit"]["own"] is False


def test_an_edit_keeps_only_the_latest_sealed_version(chat):
    c = chat["client"]
    first, second = _sealed(30), _sealed(50)
    c.put(f"{BASE}/messages/7/edit", json={"sealed": first}, headers=_as("alex"))
    c.put(f"{BASE}/messages/7/edit", json={"sealed": second}, headers=_as("alex"))
    assert _marks(chat, "alex")[0]["edit"]["sealed"] == second


# --- Only sealed values -------------------------------------------------------


@pytest.mark.parametrize("value", [
    "❤️",
    "fixed the typo",
    {"emoji": "\U0001f44d"},
    None,
    # The right fence but a payload that is not AES-GCM of the right shape.
    '```jarvis-envelope\n{"kind":"sealed","iv":"AAAA","ct":"BBBB"}\n```',
    # A real envelope with readable words beside it.
    _sealed() + "\n\nheart",
    "Love you\n\n" + _sealed(),
])
def test_anything_readable_is_refused(chat, value):
    c = chat["client"]
    assert c.post(f"{BASE}/messages/8/reactions", json={"sealed": value}, headers=_as("alex")).status_code == 422
    assert c.put(f"{BASE}/messages/7/edit", json={"sealed": value}, headers=_as("alex")).status_code == 422
    assert _marks(chat, "alex") == []


def test_what_is_stored_is_sealed_and_names_no_emoji_or_text(chat):
    _react(chat, "sam", 7)
    chat["client"].put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as("alex"))
    stored = json.loads((chat["root"] / "marks" / TOKEN / "7.json").read_text())
    assert set(stored) == {"message_id", "edit", "reactions"}
    assert set(stored["edit"]) == {"by", "at", "sealed"}
    assert set(stored["reactions"][0]) == {"id", "by", "at", "sealed", "seen_by"}
    assert private_chat_marks.is_sealed(stored["edit"]["sealed"])
    assert private_chat_marks.is_sealed(stored["reactions"][0]["sealed"])


def test_a_runaway_client_cannot_pile_reactions_on_one_message(chat):
    for _ in range(private_chat_marks.MAX_REACTIONS_PER_PERSON):
        assert _react(chat, "alex", 8).status_code == 200
    assert _react(chat, "alex", 8).status_code == 409
    assert _react(chat, "sam", 8).status_code == 200


# --- Taking a reaction back -----------------------------------------------------


def test_only_the_one_who_reacted_takes_it_back(chat):
    rid = _react(chat, "alex", 8).json()["reaction_id"]
    c = chat["client"]
    assert c.delete(f"{BASE}/messages/8/reactions/{rid}", headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/messages/8/reactions/{rid}", headers=_as("alex")).status_code == 200
    assert _marks(chat, "alex") == []
    assert not (chat["root"] / "marks" / TOKEN / "8.json").exists()
    assert c.delete(f"{BASE}/messages/8/reactions/{rid}", headers=_as("alex")).status_code == 404


# --- Seen, per member -----------------------------------------------------------


def test_a_reaction_waits_unseen_for_the_other_parent_until_their_device_shows_it(chat):
    rid = _react(chat, "sam", 7).json()["reaction_id"]
    assert _marks(chat, "sam")[0]["reactions"][0]["seen"] is True  # the one who reacted saw it
    assert _marks(chat, "alex")[0]["reactions"][0]["seen"] is False
    resp = chat["client"].post(f"{BASE}/marks/seen", json={"ids": [rid]}, headers=_as("alex"))
    assert resp.json() == {"seen": [rid]}
    assert _marks(chat, "alex")[0]["reactions"][0]["seen"] is True


def test_seen_is_kept_per_member(chat):
    rid = _react(chat, "alex", 8).json()["reaction_id"]
    # Alex marking it again changes nothing for Sam.
    chat["client"].post(f"{BASE}/marks/seen", json={"ids": [rid]}, headers=_as("alex"))
    assert _marks(chat, "sam")[0]["reactions"][0]["seen"] is False
    stored = json.loads((chat["root"] / "marks" / TOKEN / "8.json").read_text())
    assert stored["reactions"][0]["seen_by"] == ["alex"]


@pytest.mark.parametrize("ids", ["abc", ["nothex"], [1, 2], ["0" * 32] * 501])
def test_seen_takes_only_reaction_ids(chat, ids):
    assert chat["client"].post(f"{BASE}/marks/seen", json={"ids": ids}, headers=_as("alex")).status_code == 422


# --- Deleting -------------------------------------------------------------------


def test_deleting_a_message_deletes_its_edit_and_reactions(chat, monkeypatch):
    c = chat["client"]
    _react(chat, "sam", 7)
    _react(chat, "alex", 8)
    c.put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as("alex"))

    async def talk_delete(request, endpoint, payload=None, **kw):
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "message": "Message deleted."})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", talk_delete)
    resp = c.post("/api/communication/talk/messages/delete", json={"token": TOKEN, "message_id": 7}, headers=_as("alex"))
    assert resp.status_code == 200
    assert [m["message_id"] for m in _marks(chat, "alex")] == [8]


def test_a_delete_talk_refused_keeps_the_marks(chat, monkeypatch):
    _react(chat, "sam", 7)

    async def talk_refuses(request, endpoint, payload=None, **kw):
        return JSONResponse(status_code=200, content={"status": "FAILURE", "message": "Too old."})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", talk_refuses)
    chat["client"].post("/api/communication/talk/messages/delete", json={"token": TOKEN, "message_id": 7}, headers=_as("alex"))
    assert [m["message_id"] for m in _marks(chat, "alex")] == [7]


def test_deleting_the_chat_takes_every_mark(chat):
    _react(chat, "sam", 7)
    assert chat["client"].delete(BASE, headers=_as("alex")).status_code == 200
    assert not (chat["root"] / "marks" / TOKEN).exists()


# --- Notices --------------------------------------------------------------------


def test_the_notice_names_who_and_nothing_else_and_is_rate_limited(chat):
    first = _react(chat, "sam", 7)
    assert first.json()["notified"] is True
    assert _react(chat, "sam", 7).json()["notified"] is False  # within the interval: kept, not announced
    assert len(chat["notices"]) == 1
    notice = chat["notices"][0]
    assert notice["user"] == "alex"
    assert notice["body"] == "Sam reacted in your private chat"
    flat = json.dumps(notice)
    assert "jarvis-envelope" not in flat and "❤" not in flat and "\U0001f44d" not in flat
    assert {k: notice["data"][k] for k in ("token", "private", "reaction")} == {"token": TOKEN, "private": True, "reaction": True}


def test_an_edit_sends_no_notice(chat):
    chat["client"].put(f"{BASE}/messages/7/edit", json={"sealed": _sealed()}, headers=_as("alex"))
    assert chat["notices"] == []


def test_talk_reactions_stay_refused_in_a_private_room(chat):
    resp = chat["client"].post("/api/communication/talk/react",
                               json={"token": TOKEN, "message_id": 7, "reaction": "❤️"}, headers=_as("alex"))
    assert resp.status_code == 403
