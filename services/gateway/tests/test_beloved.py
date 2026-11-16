"""Beloved, the couple's page in the private chat: only one father and one mother ever reach it.

The server keeps sealed records it cannot read, answers everyone who is not
one of the two spouses with the same 404 a missing chat gets, tells only the
other spouse about a heart or a note (with no content), and forgets
everything with the chat.
"""
import base64
import json

import pytest

from services.gateway import private_chat
from services.gateway.tests.test_private_chat import PEOPLE, SEALED, _as, _create, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
BASE = f"/api/private-chat/chats/{TOKEN}/beloved"

#: Everyone in the household who is not the couple.
OUTSIDERS = {
    "admin": {"username": "admin", "display_name": "Admin", "family_role": "other", "is_minor": False,
              "nextcloud_user": "admin", "role": "admin", "is_admin": True},
    "admindad": {"username": "admindad", "display_name": "Another father", "family_role": "father", "is_minor": False,
                 "nextcloud_user": "admindad", "role": "admin", "is_admin": True},
    "manager": {"username": "manager", "display_name": "Manager", "family_role": "home_manager", "is_minor": False,
                "nextcloud_user": "manager"},
    "guest": {"username": "guest", "display_name": "Guest", "family_role": "guest", "is_minor": False,
              "nextcloud_user": "guest"},
    "household": {"username": "household", "display_name": "Household", "family_role": "other", "is_minor": False,
                  "is_system_default": True, "nextcloud_user": "household", "is_admin": True},
}
ALL = {**PEOPLE, **OUTSIDERS}
NOT_THE_COUPLE = ["kid", "teen", "gran", "uncle", "nochat", *OUTSIDERS]


def _envelope(iv: bytes = bytes(12), ct: bytes = bytes(40)) -> str:
    body = {"kind": "sealed", "title": "Encrypted message", "v": 1,
            "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}
    return f"\U0001f512 Encrypted message\n\n```jarvis-envelope\n{json.dumps(body)}\n```"


@pytest.fixture
def couple(env, monkeypatch):  # noqa: F811  (the imported fixture)
    async def people():
        return dict(ALL)

    monkeypatch.setattr(private_chat, "_people", people)
    assert _create(env).status_code == 200
    return env


def _add(e, kind="note", user="alex", **body):
    return e["client"].post(f"{BASE}/{kind}", json={"sealed": _envelope(), **body}, headers=_as(user))


# --- Who may see it -----------------------------------------------------------


def test_both_spouses_see_the_couple_and_the_records(couple):
    c = couple["client"]
    assert _add(couple, "day").status_code == 200
    for user, other in (("alex", "sam"), ("sam", "alex")):
        resp = c.get(BASE, headers=_as(user))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["me"] == user
        assert {p["username"]: p["me"] for p in data["couple"]} == {user: True, other: False}
        assert {p["family_role"] for p in data["couple"]} == {"father", "mother"}
        assert [r["kind"] for r in data["records"]] == ["day"]
        assert data["records"][0]["own"] is (user == "alex")


def test_the_chat_says_whether_beloved_is_there(couple):
    assert couple["client"].get(f"/api/private-chat/chats/{TOKEN}", headers=_as("sam")).json()["beloved"] is True


@pytest.mark.parametrize("caller", NOT_THE_COUPLE)
def test_everyone_else_gets_the_same_404(couple, caller):
    c = couple["client"]
    record = _add(couple).json()["id"]
    answers = [
        c.get(BASE, headers=_as(caller)),
        c.post(f"{BASE}/heart", json={"sealed": _envelope(), "notify": True}, headers=_as(caller)),
        c.put(f"{BASE}/note/{record}", json={"sealed": _envelope()}, headers=_as(caller)),
        c.delete(f"{BASE}/note/{record}", headers=_as(caller)),
    ]
    for resp in answers:
        assert resp.status_code == 404, (caller, resp.status_code, resp.text)
        assert resp.json()["detail"] == "Not available."
        assert "beloved" not in resp.text.lower()
    assert couple["notices"] == []


def test_a_caller_with_no_user_gets_the_same_404(couple, monkeypatch):
    from fastapi import HTTPException

    from services.gateway import main as gateway_main

    async def nobody(request, body=None):
        raise HTTPException(status_code=401, detail="Sign in first.")

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", nobody)
    resp = couple["client"].get(BASE, headers={"X-Internal-Secret": "anything"})
    assert resp.status_code == 404 and resp.json()["detail"] == "Not available."


@pytest.mark.parametrize("roles", [("father", "father"), ("mother", "mother")])
def test_a_private_chat_that_is_not_a_father_and_a_mother_has_no_beloved(couple, monkeypatch, roles):
    async def people():
        return {**ALL, "alex": {**ALL["alex"], "family_role": roles[0]}, "sam": {**ALL["sam"], "family_role": roles[1]}}

    monkeypatch.setattr(private_chat, "_people", people)
    c = couple["client"]
    assert c.get(BASE, headers=_as("alex")).status_code == 404
    assert c.get(f"/api/private-chat/chats/{TOKEN}", headers=_as("alex")).json()["beloved"] is False


def test_a_spouse_who_stops_being_a_parent_loses_it(couple, monkeypatch):
    async def people():
        return {**ALL, "sam": {**ALL["sam"], "is_minor": True}}

    monkeypatch.setattr(private_chat, "_people", people)
    assert couple["client"].get(BASE, headers=_as("sam")).status_code == 404
    # And the other spouse no longer has a couple to share it with.
    assert couple["client"].get(BASE, headers=_as("alex")).status_code == 404


# --- What is kept ---------------------------------------------------------------


@pytest.mark.parametrize("sealed", [
    "Our anniversary is on the ninth",
    SEALED.replace("AAAA", "not base64!"),
    _envelope(iv=bytes(16)),
    _envelope(ct=bytes(8)),
    {"iv": "AAAA", "ct": "BBBB"},
    None,
])
def test_only_well_formed_ciphertext_is_kept(couple, sealed):
    resp = couple["client"].post(f"{BASE}/day", json={"sealed": sealed}, headers=_as("alex"))
    assert resp.status_code == 422, resp.text
    assert not (couple["root"] / "beloved").exists()


def test_records_are_sealed_on_disk_and_capped_in_size(couple):
    resp = _add(couple, "prayer")
    assert resp.status_code == 200
    saved = json.loads((couple["root"] / "beloved" / TOKEN / "prayer" / f"{resp.json()['id']}.json").read_text())
    assert set(saved) == {"id", "kind", "by", "at", "updated_at", "updated_by", "sealed"}
    big = _envelope(ct=bytes(20_000))
    assert couple["client"].post(f"{BASE}/note", json={"sealed": big}, headers=_as("alex")).status_code == 413


def test_unknown_kinds_and_ids_never_reach_the_disk(couple):
    c = couple["client"]
    assert c.post(f"{BASE}/secrets", json={"sealed": _envelope()}, headers=_as("alex")).status_code == 404
    assert c.put(f"{BASE}/note/{'Q' * 32}", json={"sealed": _envelope()}, headers=_as("alex")).status_code == 404
    assert c.delete(f"{BASE}/note/{'z' * 32}", headers=_as("alex")).status_code == 404


def test_either_spouse_updates_shared_records_but_only_the_author_their_own(couple):
    c = couple["client"]
    prayer = _add(couple, "prayer").json()["id"]
    marked = c.put(f"{BASE}/prayer/{prayer}", json={"sealed": _envelope(ct=bytes(50))}, headers=_as("sam"))
    assert marked.status_code == 200 and marked.json()["own"] is False
    note = _add(couple, "note").json()["id"]
    assert c.put(f"{BASE}/note/{note}", json={"sealed": _envelope()}, headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/note/{note}", headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/note/{note}", headers=_as("alex")).status_code == 200


def test_deleting_the_chat_takes_beloved_with_it(couple):
    _add(couple, "day")
    assert (couple["root"] / "beloved" / TOKEN).is_dir()
    assert couple["client"].delete(f"/api/private-chat/chats/{TOKEN}", headers=_as("alex")).status_code == 200
    assert not (couple["root"] / "beloved" / TOKEN).exists()


# --- Notices --------------------------------------------------------------------


def test_a_heart_tells_only_the_other_spouse_and_nothing_else(couple):
    resp = _add(couple, "heart", notify=True)
    assert resp.status_code == 200 and resp.json()["notified"] is True
    [notice] = couple["notices"]
    assert notice["user"] == "sam"
    assert notice["category"] == "nudge"
    assert notice["body"] == "Alex is thinking of you"
    assert "token" not in notice["data"]  # not a chat notice: no unread count in the thread
    assert "Encrypted" not in json.dumps(notice)


@pytest.mark.parametrize("kind,body", [
    ("note", "Sam left you a love note"),
    ("prayer", "Sam shared a prayer request with you"),
    ("idea", "Sam has a date night idea for you"),
])
def test_notes_prayers_and_ideas_are_announced_without_content(couple, kind, body):
    assert _add(couple, kind, user="sam", notify=True).json()["notified"] is True
    assert [(n["user"], n["body"]) for n in couple["notices"]] == [("alex", body)]


@pytest.mark.parametrize("kind", ["profile", "day", "devotion"])
def test_private_bookkeeping_is_never_announced(couple, kind):
    assert _add(couple, kind, notify=True).json()["notified"] is False
    assert couple["notices"] == []


def test_hearts_are_kept_but_announced_at_most_once_a_minute(couple):
    assert _add(couple, "heart", notify=True).json()["notified"] is True
    second = _add(couple, "heart", notify=True)
    assert second.status_code == 200 and second.json()["notified"] is False
    assert len(couple["notices"]) == 1
    assert len(couple["client"].get(BASE, headers=_as("sam")).json()["records"]) == 2
