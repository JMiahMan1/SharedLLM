"""The parents' weekly recap: built from what was reported, never padded."""

from datetime import date

from services.common.guardian_recap import build, summary_line, week_bounds

START, END = week_bounds(date(2026, 10, 21))  # Mon 19 .. Sun 25 Oct


def _usage(day, minutes, apps=()):
    return {"day": day, "millis": minutes * 60000, "apps": [{"package": p, "millis": m * 60000} for p, m in apps]}


def test_week_bounds_are_monday_to_sunday():
    assert (START.isoformat(), END.isoformat()) == ("2026-10-19", "2026-10-25")


def test_a_full_week():
    usage = [
        _usage("2026-10-12", 60),  # last week
        _usage("2026-10-19", 120, [("com.google.android.youtube", 70), ("com.spotify.music", 20)]),
        _usage("2026-10-20", 180, [("com.google.android.youtube", 100)]),
    ]
    events = [
        {"feature": "media", "item": "Lecrae — Blessings", "at": "2026-10-19T20:00:00Z"},
        {"feature": "media", "item": "Lecrae — Blessings", "at": "2026-10-20T20:00:00Z"},
        {"feature": "web", "item": "tiktok.com", "at": "2026-10-20T21:00:00Z", "detail": {"visits": 0, "blocked": 3}},
        {"feature": "web", "item": "khanacademy.org", "at": "2026-10-20T21:00:00Z", "detail": {"visits": 12, "blocked": 0}},
        {"feature": "sms", "item": "Grandma", "at": "2026-10-21T10:00:00Z", "detail": {"body": "secret text"}},
        {"feature": "sms", "item": "Grandma", "at": "2026-10-21T11:00:00Z"},
        {"feature": "calls", "item": "Dad", "at": "2026-10-21T12:00:00Z"},
        {"feature": "bible", "item": "John 3", "at": "2026-10-19T07:00:00Z"},
        {"feature": "bible", "item": "John 4", "at": "2026-10-22T07:00:00Z"},
        {"feature": "media", "item": "old", "at": "2026-10-10T07:00:00Z"},
    ]
    concerns = [
        {"category": "bullying", "label": "Bullying", "level": "high", "excerpt": "nobody likes you", "feature": "sms", "at": "2026-10-21T10:00:00Z"},
        {"category": "self_harm", "label": "Self-harm or suicide", "level": "severe", "excerpt": "want to die", "feature": "sms", "at": "2026-10-22T10:00:00Z"},
    ]
    r = build(usage, events, concerns, START, END)
    assert r["screen"] == {"total_min": 300, "daily_avg_min": 150, "days_reported": 2, "prev_total_min": 60,
                           "change_pct": 400, "busiest_day": "2026-10-20", "busiest_min": 180, "late_nights": 0}
    assert r["top_apps"][0] == {"package": "com.google.android.youtube", "minutes": 170}
    assert r["media"] == [{"title": "Lecrae — Blessings", "count": 2}]
    assert r["web"] == {"top": [{"domain": "khanacademy.org", "visits": 12}], "blocked": [{"domain": "tiktok.com", "count": 3}]}
    assert r["comms"] == {"calls": 1, "call_contacts": 1, "messages": 2, "message_contacts": 1}
    assert "secret text" not in str(r["comms"])
    assert r["faith"] == {"bible_days": 2}
    assert [c["category"] for c in r["concerns"]] == ["self_harm", "bullying"]  # severe first
    assert "Read the Bible on 2 days" in r["highlights"]
    assert summary_line("Emma", r) == "Emma's week: about 2 h 30 min a day on the phone, most in Youtube, 2 concerns to look at."


def test_an_empty_week_says_so():
    r = build([], [], [], START, END)
    assert r["has_data"] is False and r["screen"] is None and r["top_apps"] == []
    assert summary_line("Emma", r) == "Nothing was reported from Emma's phone this week."


def test_late_nights_count_in_household_time():
    # 23:30 Phoenix on the 20th is 06:30 UTC on the 21st.
    late = {"day": "2026-10-20", "millis": 60000, "last_use": 1792564200000}
    early = {"day": "2026-10-21", "millis": 60000, "last_use": 1792627200000}  # 21 Oct 17:00 Phoenix
    r = build([late, early], [], [], START, END, "America/Phoenix")
    assert r["screen"]["late_nights"] == 1


def test_a_parents_question_names_one_of_their_children():
    from services.common.guardian_recap import asked_about

    kids = [{"username": "emma", "display_name": "Emma"}, {"username": "sam", "display_name": "Samuel"}]
    assert asked_about("How was Emma's week?", kids)["username"] == "emma"
    assert asked_about("how is samuel doing", kids)["username"] == "sam"
    assert asked_about("how was the weather this week", kids) is None
    assert asked_about("play emma's playlist", kids) is None
    assert asked_about("how was Emmanuel's week", kids) is None


def test_the_spoken_recap():
    from services.common.guardian_recap import spoken

    usage = [_usage("2026-10-19", 120, [("com.google.android.youtube", 70)])]
    concerns = [{"category": "bullying", "label": "Bullying", "level": "high", "at": "2026-10-21T10:00:00Z"}]
    text = spoken("Emma", build(usage, [], concerns, START, END))
    assert text.startswith("Emma has averaged 2 h 0 min a day on the phone.")
    assert "Most used: Youtube." in text and "bullying (high)" in text
    assert spoken("Emma", build([], [], [], START, END)) == "Nothing has been reported from Emma's phone yet this week."
