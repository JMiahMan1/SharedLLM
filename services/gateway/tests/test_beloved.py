"""Beloved, the couple's page in the private chat: only one father and one mother ever reach it.

The server keeps sealed records it cannot read, answers everyone who is not
one of the two spouses with the same 404 a missing chat gets, tells only the
other spouse about a heart or a note (with no content), and forgets
everything with the chat.
"""
import base64
import json

import pytest

from services.gateway import beloved, private_chat
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
    _children_answer(monkeypatch, 200, {"children": []})
    assert _create(env).status_code == 200
    env["redis"].kv.clear()  # a fresh chat never inherits another test's notice limits
    return env


def _children_answer(monkeypatch, status, body):
    """What identity's internal children list says (never a real request)."""
    from services.gateway import app_vetting_routes

    async def identity_call(method, path, request=None, **kwargs):
        assert (method, path) == ("GET", "/api/internal/guardian/children")
        return status, body

    monkeypatch.setattr(app_vetting_routes, "identity_call", identity_call)


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


@pytest.mark.parametrize("kind", ["devotion"])
def test_private_bookkeeping_is_never_announced(couple, kind):
    assert _add(couple, kind, notify=True).json()["notified"] is False
    assert couple["notices"] == []


def test_hearts_are_kept_but_announced_at_most_once_a_minute(couple):
    assert _add(couple, "heart", notify=True).json()["notified"] is True
    second = _add(couple, "heart", notify=True)
    assert second.status_code == 200 and second.json()["notified"] is False
    assert len(couple["notices"]) == 1
    assert len(couple["client"].get(BASE, headers=_as("sam")).json()["records"]) == 2


# --- Just us: the couple's private calendar --------------------------------------

#: Words that must never reach a lock screen, a log line or anything a notice carries.
REVEALING = ("intima", "sex", "night", "together", "bed", "romanc", "romantic")


def test_the_private_calendar_kinds_are_kept_sealed(couple):
    for kind in ("together", "plan"):
        resp = _add(couple, kind)
        assert resp.status_code == 200, resp.text
        saved = json.loads((couple["root"] / "beloved" / TOKEN / kind / f"{resp.json()['id']}.json").read_text())
        assert set(saved) == {"id", "kind", "by", "at", "updated_at", "updated_by", "sealed"}
    kinds = sorted(r["kind"] for r in couple["client"].get(BASE, headers=_as("sam")).json()["records"])
    assert kinds == ["plan", "together"]


@pytest.mark.parametrize("caller", NOT_THE_COUPLE)
@pytest.mark.parametrize("kind", ["together", "plan"])
def test_the_private_calendar_is_a_404_for_everyone_else(couple, caller, kind):
    c = couple["client"]
    record = _add(couple, kind).json()["id"]
    answers = [
        c.get(BASE, headers=_as(caller)),
        c.post(f"{BASE}/{kind}", json={"sealed": _envelope(), "notify": True}, headers=_as(caller)),
        c.put(f"{BASE}/{kind}/{record}", json={"sealed": _envelope()}, headers=_as(caller)),
        c.delete(f"{BASE}/{kind}/{record}", headers=_as(caller)),
    ]
    for resp in answers:
        assert resp.status_code == 404, (caller, resp.status_code, resp.text)
        assert resp.json()["detail"] == "Not available."
        assert kind not in resp.text
    assert couple["notices"] == []
    assert (couple["root"] / "beloved" / TOKEN / kind / f"{record}.json").is_file()


def test_a_marked_day_is_only_its_authors_but_a_plan_is_either_spouses(couple):
    c = couple["client"]
    marked = _add(couple, "together").json()["id"]
    assert c.put(f"{BASE}/together/{marked}", json={"sealed": _envelope()}, headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/together/{marked}", headers=_as("sam")).status_code == 403
    assert c.delete(f"{BASE}/together/{marked}", headers=_as("alex")).status_code == 200
    plan = _add(couple, "plan").json()["id"]
    done = c.put(f"{BASE}/plan/{plan}", json={"sealed": _envelope(ct=bytes(60))}, headers=_as("sam"))
    assert done.status_code == 200 and done.json()["own"] is False
    assert c.delete(f"{BASE}/plan/{plan}", headers=_as("sam")).status_code == 200


def test_a_plan_tells_the_other_spouse_only_that_something_is_waiting(couple):
    assert _add(couple, "plan", user="sam", notify=True).json()["notified"] is True


def test_plans_are_announced_at_most_once_in_half_a_minute(couple):
    assert _add(couple, "plan", notify=True).json()["notified"] is True
    # A plan's update wording is the same say-nothing line, and the same limit applies.
    record = couple["client"].get(BASE, headers=_as("alex")).json()["records"][0]["id"]
    assert couple["client"].put(f"{BASE}/plan/{record}", json={"sealed": _envelope(), "notify": True},
                                headers=_as("alex")).json()["notified"] is False
    assert len(couple["notices"]) == 1


def test_a_marked_day_update_is_never_announced(couple):
    marked = _add(couple, "together").json()["id"]
    # An update without ``notify`` changes the sealed contents and announces nothing.
    resp = couple["client"].put(f"{BASE}/together/{marked}", json={"sealed": _envelope()}, headers=_as("alex"))
    assert resp.status_code == 200 and "notified" not in resp.json()
    assert couple["notices"] == []


def test_several_marks_are_kept_apart_and_changed_one_at_a_time(couple):
    """The day is sealed, so the server cannot (and must not) merge marks: two
    on the same day are two records, each edited or removed on its own."""
    c = couple["client"]
    first = _add(couple, "together").json()["id"]
    second = _add(couple, "together").json()["id"]
    third = _add(couple, "together", user="sam").json()["id"]
    assert len({first, second, third}) == 3
    edited = c.put(f"{BASE}/together/{second}", json={"sealed": _envelope(ct=bytes(60))}, headers=_as("alex"))
    assert edited.status_code == 200
    assert c.delete(f"{BASE}/together/{first}", headers=_as("alex")).status_code == 200
    rows = {r["id"]: r for r in c.get(BASE, headers=_as("sam")).json()["records"] if r["kind"] == "together"}
    assert set(rows) == {second, third}
    assert rows[second]["sealed"] == _envelope(ct=bytes(60))
    assert rows[third]["sealed"] == _envelope()


def test_an_unreadable_record_is_logged_without_its_kind(couple, caplog):
    folder = couple["root"] / "beloved" / TOKEN / "together"
    folder.mkdir(parents=True)
    (folder / f"{'e' * 32}.json").write_text("{not json")
    with caplog.at_level("WARNING", logger="gateway.beloved"):
        assert couple["client"].get(BASE, headers=_as("alex")).status_code == 200
    assert "unreadable record" in caplog.text
    for word in REVEALING:
        assert word not in caplog.text.lower(), word


# --- The children's birthdays -------------------------------------------------------


def test_each_parent_is_offered_only_their_own_children_and_birth_dates(couple, monkeypatch):
    _children_answer(monkeypatch, 200, {"children": [
        {"username": "kid", "display_name": "Kid", "parents": ["alex", "sam"], "birth_date": "2016-04-02"},
        {"username": "teen", "display_name": "Teen", "parents": ["alex", "sam"], "birth_date": None},
        {"username": "cousin", "display_name": "Cousin", "parents": ["uncle"], "birth_date": "2015-01-01"},
    ]})
    data = couple["client"].get(BASE, headers=_as("sam")).json()
    assert data["children"] == [
        {"username": "kid", "display_name": "Kid", "birth_date": "2016-04-02"},
        {"username": "teen", "display_name": "Teen", "birth_date": None},
    ]


def test_beloved_still_opens_when_identity_cannot_list_the_children(couple, monkeypatch):
    _children_answer(monkeypatch, 503, None)
    resp = couple["client"].get(BASE, headers=_as("alex"))
    assert resp.status_code == 200
    assert resp.json()["children"] == []


# --- New kinds of notice ---------------------------------------------------------


@pytest.mark.parametrize("kind,body", [
    ("day", "Sam added a special day"),
    ("together", "Something from Sam is waiting"),
    ("profile", "Sam updated your Beloved page"),
])
def test_the_new_kinds_are_announced_without_content(couple, kind, body):
    assert _add(couple, kind, user="sam", notify=True).json()["notified"] is True
    [(notice)] = couple["notices"]
    assert notice["user"] == "alex"
    assert notice["category"] == "nudge"
    assert notice["title"] == "Beloved"
    assert notice["body"] == body
    assert "token" not in notice["data"]


def test_a_day_and_profile_are_announced_at_most_once_a_minute(couple):
    assert _add(couple, "day", notify=True).json()["notified"] is True
    assert _add(couple, "day", notify=True).json()["notified"] is False
    assert _add(couple, "profile", notify=True).json()["notified"] is True
    assert len(couple["notices"]) == 2


@pytest.mark.parametrize("kind,body", [
    ("day", "Alex updated a special day"),
    ("plan", "Alex updated your plans"),
    ("together", "Something from Alex is waiting"),
    ("prayer", "Alex answered a prayer request"),
    ("idea", "Alex updated a date night idea"),
])
def test_an_update_tells_the_spouse_with_update_wording(couple, kind, body):
    c = couple["client"]
    record = _add(couple, kind).json()["id"]
    resp = c.put(f"{BASE}/{kind}/{record}", json={"sealed": _envelope(), "notify": True}, headers=_as("alex"))
    assert resp.status_code == 200 and resp.json()["notified"] is True
    [(notice)] = couple["notices"]
    assert notice["user"] == "sam"
    assert notice["category"] == "nudge"
    assert notice["body"] == body
    assert "Encrypted" not in json.dumps(notice)


def test_an_update_without_notify_stays_silent(couple):
    c = couple["client"]
    record = _add(couple, "day").json()["id"]
    resp = c.put(f"{BASE}/day/{record}", json={"sealed": _envelope()}, headers=_as("alex"))
    assert resp.status_code == 200 and "notified" not in resp.json()
    assert couple["notices"] == []


# --- Seen / read signals, activity and preferences -------------------------------


def test_gallery_seen_tells_the_spouse_once_in_half_an_hour(couple):
    c = couple["client"]
    first = c.post(f"{BASE}/seen", json={"what": "gallery"}, headers=_as("alex"))
    assert first.status_code == 200 and first.json() == {"shared": True, "notified": True}
    [(notice)] = couple["notices"]
    assert notice["user"] == "sam"
    assert notice["body"] == "Alex is looking through your photos"
    # Again within the limit: recorded, but not announced a second time.
    second = c.post(f"{BASE}/seen", json={"what": "gallery"}, headers=_as("alex"))
    assert second.json() == {"shared": True, "notified": False}
    assert len(couple["notices"]) == 1


@pytest.mark.parametrize("what,body", [
    ("note", "Sam read your love note"),
    ("prayer", "Sam read your prayer request"),
    ("idea", "Sam saw your date night idea"),
])
def test_reading_a_note_prayer_or_idea_is_announced_once_in_ten_minutes(couple, what, body):
    c = couple["client"]
    assert c.post(f"{BASE}/seen", json={"what": what}, headers=_as("sam")).json()["notified"] is True
    [(notice)] = couple["notices"]
    assert notice["user"] == "alex" and notice["body"] == body
    assert c.post(f"{BASE}/seen", json={"what": what}, headers=_as("sam")).json()["notified"] is False
    assert len(couple["notices"]) == 1


def test_a_page_visit_is_recorded_but_never_announced(couple):
    resp = couple["client"].post(f"{BASE}/seen", json={"what": "page"}, headers=_as("alex"))
    assert resp.status_code == 200 and resp.json() == {"shared": True, "notified": False}
    assert couple["notices"] == []


def test_share_activity_off_records_and_sends_nothing(couple):
    c = couple["client"]
    assert c.put(f"{BASE}/prefs", json={"share_activity": False}, headers=_as("alex")).json()["share_activity"] is False
    resp = c.post(f"{BASE}/seen", json={"what": "gallery"}, headers=_as("alex"))
    assert resp.json() == {"shared": False, "notified": False}
    assert couple["notices"] == []
    assert c.get(f"{BASE}/activity", headers=_as("sam")).json()["activity"] == []


@pytest.mark.parametrize("what", ["video", "", None, "GALLERY"])
def test_an_unknown_seen_signal_is_refused(couple, what):
    resp = couple["client"].post(f"{BASE}/seen", json={"what": what}, headers=_as("alex"))
    assert resp.status_code == 422


def test_the_activity_log_is_metadata_only_newest_first_and_capped(couple):
    c = couple["client"]
    for _ in range(205):
        assert c.post(f"{BASE}/seen", json={"what": "page"}, headers=_as("alex")).status_code == 200
    data = c.get(f"{BASE}/activity", headers=_as("sam")).json()
    assert len(data["activity"]) == 50
    # Every entry is metadata only: who, what and when, and never anything sealed.
    for entry in data["activity"]:
        assert set(entry) == {"who", "who_name", "what", "at"}
        assert entry["who"] == "alex" and entry["what"] == "page"
    assert "Encrypted" not in json.dumps(data)
    # The log on disk is capped at ACTIVITY_KEEP.
    log = json.loads((couple["root"] / "beloved" / TOKEN / "activity.json").read_text())
    assert len(log) == beloved.ACTIVITY_KEEP


@pytest.mark.parametrize("caller", NOT_THE_COUPLE)
def test_activity_seen_and_prefs_are_a_404_for_everyone_else(couple, caller):
    c = couple["client"]
    answers = [
        c.get(f"{BASE}/activity", headers=_as(caller)),
        c.post(f"{BASE}/seen", json={"what": "page"}, headers=_as(caller)),
        c.get(f"{BASE}/prefs", headers=_as(caller)),
        c.put(f"{BASE}/prefs", json={"share_activity": False}, headers=_as(caller)),
    ]
    for resp in answers:
        assert resp.status_code == 404, (caller, resp.status_code, resp.text)
        assert resp.json()["detail"] == "Not available."
    assert couple["notices"] == []


def test_prefs_default_and_update_only_the_caller(couple):
    c = couple["client"]
    assert c.get(f"{BASE}/prefs", headers=_as("alex")).json() == {
        "share_activity": True, "encouragements": True, "encourage_hour": 19}
    updated = c.put(f"{BASE}/prefs", json={"encouragements": False, "encourage_hour": 8}, headers=_as("alex")).json()
    assert updated == {"share_activity": True, "encouragements": False, "encourage_hour": 8}
    # The other spouse's preferences are untouched.
    assert c.get(f"{BASE}/prefs", headers=_as("sam")).json()["encouragements"] is True


@pytest.mark.parametrize("body", [{"encourage_hour": 6}, {"encourage_hour": 22}, {"encourage_hour": "x"},
                                  {"share_activity": "yes"}, {"encouragements": 1}])
def test_bad_preferences_are_refused(couple, body):
    resp = couple["client"].put(f"{BASE}/prefs", json=body, headers=_as("alex"))
    assert resp.status_code == 422
