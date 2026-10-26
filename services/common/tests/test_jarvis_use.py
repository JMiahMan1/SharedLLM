"""What a person does most inside Jarvis: grouped, ranked, never guessed."""

from datetime import date

from services.common.jarvis_use import summarize

TODAY = date(2026, 10, 25)


def test_groups_and_ranks_the_in_app_features():
    events = [
        {"feature": "media", "at": "2026-10-24"},
        {"feature": "music", "at": "2026-10-24"},
        {"feature": "bible", "at": "2026-10-25"},
        {"feature": "notes", "at": "2026-10-25"},
        {"feature": "recipes", "at": "2026-10-25"},
    ]
    out = summarize(events, days=7, today=TODAY)
    assert out["has_data"] is True
    assert out["total"] == 5
    assert out["window_start"] == "2026-10-19"
    # Media groups music, video and podcasts into one line; the rest are single.
    assert out["top"][0] == {"label": "Music, video and podcasts", "count": 2}
    assert {row["label"] for row in out["top"]} == {"Music, video and podcasts", "Bible", "Notes", "Recipes"}


def test_external_collection_is_left_out_of_a_members_own_summary():
    events = [
        {"feature": "sms", "at": "2026-10-25"},
        {"feature": "calls", "at": "2026-10-25"},
        {"feature": "web", "at": "2026-10-25"},
        {"feature": "dashboard", "at": "2026-10-25"},
    ]
    out = summarize(events, days=7, today=TODAY)
    assert out["total"] == 1
    assert out["top"] == [{"label": "Dashboard", "count": 1}]


def test_only_the_window_counts_and_its_start_is_said_out_loud():
    events = [
        {"feature": "notes", "at": "2026-10-19"},  # first day of a 7-day window
        {"feature": "notes", "at": "2026-10-18"},  # one day too early
        {"feature": "notes", "at": "2026-10-26"},  # tomorrow
    ]
    out = summarize(events, days=7, today=TODAY)
    assert out["total"] == 1
    assert out["window_start"] == "2026-10-19"


def test_nothing_recorded_is_empty_not_zero():
    out = summarize([], days=7, today=TODAY)
    assert out["has_data"] is False
    assert out["total"] == 0
    assert out["top"] == []


def test_an_unknown_feature_keeps_its_own_name():
    out = summarize([{"feature": "arcade", "at": "2026-10-25"}], days=7, today=TODAY)
    assert out["top"] == [{"label": "Arcade", "count": 1}]


def test_bad_rows_and_times_are_skipped():
    events = [
        "not a dict",
        {"feature": "", "at": "2026-10-25"},
        {"feature": "notes", "at": "not a time"},
        {"feature": "notes", "at": "2026-10-25"},
    ]
    out = summarize(events, days=7, today=TODAY)  # type: ignore[arg-type]
    assert out["total"] == 1
    assert out["top"] == [{"label": "Notes", "count": 1}]
