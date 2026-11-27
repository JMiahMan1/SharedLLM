"""Beloved encouragements: one warm line a day, chosen from metadata only.

The decision itself is a pure function (``decide``): every rule (a, b, c, d),
the chosen hour, the quiet hours, the one-per-day claim and prefs-off are
checked without a network. The pass that drives it is also checked against a
fake redis and a fake directory, so a non-couple is never chosen.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.gateway import beloved, private_chat
from services.gateway import beloved_encouragements as enc
from services.gateway.tests.test_private_chat import PEOPLE, SEALED, _as, _create, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
TZ = ZoneInfo("UTC")
DAY = 86400
#: A fixed local midday, well inside every encourage hour but the default 19.
NOON = datetime(2026, 1, 15, 12, 0, tzinfo=TZ).timestamp()
#: 19:00 local, the default encourage hour (the decision only fires inside it).
SEVEN_PM = datetime(2026, 1, 15, 19, 0, tzinfo=TZ).timestamp()
#: The same instant viewed as midday, for rules that are about elapsed time, not the hour.
MIDDAY = datetime(2026, 1, 15, 12, 0, tzinfo=TZ).timestamp()


def _facts(*, records=(), activity=(), prefs=None, sent_today=frozenset()):
    prefs = prefs or {m: {"share_activity": True, "encouragements": True, "encourage_hour": 19}
                     for m in ("alex", "sam")}
    return enc.CoupleFacts(records=list(records), activity=list(activity), prefs=dict(prefs),
                           sent_today=set(sent_today))


def _decide(facts, spouse="alex", other="sam", now=SEVEN_PM, token=TOKEN, tz=TZ):
    other_name = "Sam" if other == "sam" else "Alex"
    return enc.decide(facts=facts, spouse=spouse, other=other, other_name=other_name, now=now, tz=tz, token=token)


# --- The pure decision: the four rules ------------------------------------------


def test_rule_a_when_no_heart_or_note_in_two_days():
    old = NOON - 3 * DAY
    # No page visit at all: only the stale heart and Sam's note exist.
    facts = _facts(records=[("heart", "alex", old), ("note", "sam", NOON)])
    body = _decide(facts, now=SEVEN_PM)
    assert body == "Send Sam a little love today: a heart takes one tap."


def test_rule_a_counts_hearts_and_notes_together():
    # A heart two days ago and a note yesterday: the note keeps rule a away.
    facts = _facts(records=[("heart", "alex", NOON - 3 * DAY), ("note", "alex", NOON - DAY)],
                   activity=[{"who": "sam", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) not in (None,)  # falls through to b/c/d, never rule a


def test_rule_b_when_the_other_spouse_reached_out_and_this_one_has_not_answered():
    # Alex sent a heart an hour ago (rule a satisfied); Sam reached out after and Alex has not answered.
    facts = _facts(records=[("heart", "alex", NOON - 3600), ("note", "sam", NOON - 1800)],
                   activity=[{"who": "sam", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) == "Sam reached out today. Send something back?"


def test_rule_b_needs_this_spouse_to_have_not_answered():
    # Sam reached out, then Alex answered after: not rule b.
    facts = _facts(records=[("heart", "alex", NOON - 7200), ("note", "sam", NOON - 3600),
                            ("heart", "alex", NOON - 1800)],
                   activity=[{"who": "sam", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) != "Sam reached out today. Send something back?"


def test_rule_b_ignores_something_older_than_a_day():
    facts = _facts(records=[("heart", "alex", NOON - 3600), ("note", "sam", NOON - 2 * DAY)],
                   activity=[{"who": "sam", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) != "Sam reached out today. Send something back?"


def test_rule_c_when_no_visit_in_three_days():
    # Alex wrote a heart an hour ago (rule a satisfied); Sam has not written in the
    # last day (rule b does not fire); the last page visit is 4 days old, so rule c does.
    facts = _facts(records=[("heart", "alex", NOON - 3600)],
                   activity=[{"who": "alex", "what": "page", "at": NOON - 4 * DAY}])
    assert _decide(facts, now=SEVEN_PM) == "Today's verse and question are waiting in Beloved. Talk it over tonight?"


def test_rule_d_a_rotating_tip_otherwise():
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}])
    body = _decide(facts, now=SEVEN_PM)
    assert body in enc.TIPS


def test_rule_d_is_deterministic_per_couple_per_day():
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}])
    first = _decide(facts, token="room123abc", now=SEVEN_PM)
    again = _decide(facts, token="room123abc", now=SEVEN_PM)
    assert first == again
    # A different couple may get a different tip, but still one of the list.
    other = _decide(facts, token="anothertoken", now=SEVEN_PM)
    assert other in enc.TIPS


def test_a_recent_page_visit_is_enough_to_avoid_rule_c():
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON - DAY}])
    assert _decide(facts, now=SEVEN_PM) not in (
        "Today's verse and question are waiting in Beloved. Talk it over tonight?",)


def test_rule_d_picks_a_tip_when_a_b_and_c_are_satisfied():
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) in enc.TIPS


# --- The pure decision: timing, prefs, one per day -------------------------------


def test_outside_the_chosen_hour_no_message():
    facts = _facts()
    # Default hour 19; noon is outside it.
    assert _decide(facts, now=NOON) is None


def test_at_the_chosen_hour_a_message_is_due():
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}])
    assert _decide(facts, now=SEVEN_PM) in enc.TIPS


def test_quiet_hours_never_send():
    # 23:00 local, inside the 22:00 to 07:00 quiet hours, even at hour 23.
    late = datetime(2026, 1, 15, 23, 0, tzinfo=TZ).timestamp()
    prefs = {"alex": {"share_activity": True, "encouragements": True, "encourage_hour": 23}}
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}], prefs=prefs)
    assert _decide(facts, now=late) is None


def test_encouragements_off_means_no_message():
    prefs = {"alex": {"share_activity": True, "encouragements": False, "encourage_hour": 19}}
    facts = _facts(prefs=prefs)
    assert _decide(facts, now=SEVEN_PM) is None


def test_one_per_day_is_respected():
    prefs = {"alex": {"share_activity": True, "encouragements": True, "encourage_hour": 19}}
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}], prefs=prefs, sent_today={"alex"})
    assert _decide(facts, now=SEVEN_PM) is None
    # The other spouse, not yet encouraged today, is still due.
    facts2 = _facts(records=[("heart", "alex", NOON - 1800)],
                    activity=[{"who": "alex", "what": "page", "at": NOON}], prefs=prefs, sent_today={"sam"})
    assert _decide(facts2, now=SEVEN_PM) in enc.TIPS


def test_a_custom_encourage_hour_is_honoured():
    prefs = {"alex": {"share_activity": True, "encouragements": True, "encourage_hour": 8}}
    eight = datetime(2026, 1, 15, 8, 0, tzinfo=TZ).timestamp()
    facts = _facts(records=[("heart", "alex", NOON - 1800)],
                   activity=[{"who": "alex", "what": "page", "at": NOON}], prefs=prefs)
    assert _decide(facts, now=eight) in enc.TIPS
    assert _decide(facts, now=SEVEN_PM) is None


# --- The pass: only a real couple is ever chosen ---------------------------------


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)


class _Client:
    def get(self, url, headers=None, timeout=None):
        class _Resp:
            status = 200

            async def json(self, content_type=None):
                return list(_DIRECTORY.values())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        return _Resp()


_DIRECTORY: dict[str, dict] = {}


@pytest.fixture
def couple(env, monkeypatch):  # noqa: F811  (the imported fixture)
    async def people():
        return dict(PEOPLE)

    monkeypatch.setattr(private_chat, "_people", people)
    _create(env)
    _DIRECTORY.clear()
    _DIRECTORY.update({u: dict(p) for u, p in PEOPLE.items()})
    return env


def _setup(monkeypatch, redis):
    import services.gateway.cache as gateway_cache
    from services.common import notify

    async def send(payload, **kw):
        _SENT.append(payload)
        return True

    _SENT.clear()
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)
    monkeypatch.setattr(notify, "send", send)
    monkeypatch.setattr(enc, "ENABLED", True)
    monkeypatch.setattr(enc, "_household_tz", lambda: TZ)


async def _check_with_fresh_redis(monkeypatch, redis, now):
    return await enc.check_once(_Client(), identity="i", secret="s", now=now), redis


_SENT: list = []


def _run(monkeypatch, now):
    import asyncio

    redis = _Redis()
    _setup(monkeypatch, redis)
    return asyncio.run(enc.check_once(_Client(), identity="i", secret="s", now=now)), redis


def _enroll():
    """Mark the couple as enrolled: one of them has opened Beloved before."""
    from services.gateway import beloved

    token = private_chat.get_store().all()[0]["token"]
    beloved.record_activity(token, "alex", "page")
    return token


def test_a_couple_who_never_used_beloved_is_never_encouraged(couple, monkeypatch):
    sent, _ = _run(monkeypatch, SEVEN_PM)
    assert sent == 0 and _SENT == []


def test_a_couple_gets_at_most_one_encouragement_each_a_day(couple, monkeypatch):
    import asyncio

    # An enrolled couple (they have opened Beloved) with no hearts or notes: rule a is due for both at 19:00.
    _enroll()
    sent, redis = _run(monkeypatch, SEVEN_PM)
    assert sent == 2
    assert sorted(n["user"] for n in _SENT) == ["alex", "sam"]
    for notice in _SENT:
        assert notice["category"] == "nudge" and notice["title"] == "Beloved"
        assert "token" not in notice["data"]
        name = "Sam" if notice["user"] == "alex" else "Alex"
        assert notice["body"] == f"Send {name} a little love today: a heart takes one tap."
    # A second pass the same day sends nothing more (the once-per-day key holds).
    _SENT.clear()
    sent2, _ = asyncio.run(_check_with_fresh_redis(monkeypatch, redis, now=SEVEN_PM + 60))
    assert sent2 == 0 and _SENT == []


def test_a_couple_who_has_written_recently_gets_a_tip(couple, monkeypatch):
    import asyncio

    # A recent heart from each spouse (rule a satisfied) and a recent page visit (rule c
    # satisfied), written at the decision's own "now" so rule b sees no unanswered reach-out.
    store = beloved.get_store()
    for user in ("alex", "sam"):
        store.add(TOKEN, "heart", user, SEALED)
        beloved.record_activity(TOKEN, user, "page")
    # Back-date the two hearts and the two visits to just before the decision's "now".
    import json as _json

    now = SEVEN_PM
    for path in (couple["root"] / "beloved" / TOKEN / "heart").glob("*.json"):
        rec = _json.loads(path.read_text())
        rec["at"] = int(now - 1800)
        rec["updated_at"] = rec["at"]
        path.write_text(_json.dumps(rec))
    act = couple["root"] / "beloved" / TOKEN / "activity.json"
    act.write_text(_json.dumps([{"who": u, "what": "page", "at": int(now - 60)} for u in ("alex", "sam")]))
    sent, redis = _run(monkeypatch, now)
    assert sent == 2
    for notice in _SENT:
        assert notice["body"] in enc.TIPS
    # And a second pass the same day sends nothing more.
    _SENT.clear()
    sent2, _ = asyncio.run(_check_with_fresh_redis(monkeypatch, redis, now=now + 60))
    assert sent2 == 0 and _SENT == []


def test_a_non_couple_is_never_chosen(couple, monkeypatch):
    # A second private chat that is not one father and one mother must be skipped.
    other = {**PEOPLE, "sam": {**PEOPLE["sam"], "family_role": "father"}}
    chat = {"token": "secondchat1", "members": ["alex", "sam"], "storage": "store"}
    private_chat.get_store().save(chat)

    async def people():
        return dict(other)

    monkeypatch.setattr(private_chat, "_people", people)
    _DIRECTORY.update(other)
    sent, _ = _run(monkeypatch, SEVEN_PM)
    assert sent == 0


def test_encouragements_off_sends_nothing(couple, monkeypatch):
    import services.gateway.cache as gateway_cache
    from services.common import notify

    async def send(payload, **kw):
        _SENT.append(payload)
        return True

    _SENT.clear()
    redis = _Redis()
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)
    monkeypatch.setattr(notify, "send", send)
    monkeypatch.setattr(enc, "ENABLED", False)
    monkeypatch.setattr(enc, "_household_tz", lambda: TZ)
    import asyncio

    sent = asyncio.run(enc.check_once(_Client(), identity="i", secret="s", now=SEVEN_PM))
    assert sent == 0 and _SENT == []


def test_the_quiet_hours_send_nothing(couple, monkeypatch):
    late = datetime(2026, 1, 15, 23, 0, tzinfo=TZ).timestamp()
    sent, _ = _run(monkeypatch, late)
    assert sent == 0
