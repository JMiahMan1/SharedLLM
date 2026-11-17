"""While a child's phone is locked: who it may still call (the parents), and
the extra apps it suspends (so a paired watch is no way around the lock).
Bedtime is a full lock for its whole window. Fake data only."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.common.screen_time import (
    NEVER_BLOCKED_CORE,
    evaluate,
    lock_call_allow,
    lock_suspend_apps,
    normalize_phone,
    parent_contacts,
)

TZ = ZoneInfo("Etc/GMT+7")
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=TZ)  # a Wednesday afternoon


@pytest.mark.parametrize(
    ("raw", "cc", "expected"),
    [
        ("+1 (555) 010-0199", "", "+15550100199"),
        ("(555) 010-0199", "1", "+15550100199"),
        ("555.010.0199", "1", "+15550100199"),
        ("1-555-010-0199", "1", "+15550100199"),
        ("0044 20 7946 0999", "", "+442079460999"),
        ("+44 20 7946 0999", "1", "+442079460999"),
        ("020 7946 0999", "44", "+442079460999"),
        ("555-010-0199 x12", "1", "+15550100199"),
        ("555-010-0199 ext. 12", "1", "+15550100199"),
        ("555-010-0199;12", "1", "+15550100199"),
    ],
)
def test_numbers_in_any_format_become_e164(raw, cc, expected):
    assert normalize_phone(raw, cc) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "call me", "911", "12345", "+1234567890123456", 5550100199])
def test_what_is_not_a_phone_number_is_none(raw):
    assert normalize_phone(raw, "1") is None


def test_a_local_number_with_no_household_country_code_is_not_guessed():
    assert normalize_phone("(555) 010-0199") is None
    assert normalize_phone("(555) 010-0199", "") is None


def test_the_call_list_is_e164_without_duplicates_or_junk():
    assert lock_call_allow(["+1 555 010 0199", "+15550100199", "nope", "", "+15550100198"]) == [
        "+15550100199",
        "+15550100198",
    ]
    assert lock_call_allow(None) == []
    assert lock_call_allow("+15550100199") == [], "a bare string is not a list of numbers"


def test_parent_contacts_keep_each_parent_and_say_when_there_is_no_number():
    assert parent_contacts([
        {"name": "Test Dad", "phone": "+15550100101"},
        {"name": "Test Mom", "phone": None},
        {"name": "", "phone": "+15550100103"},
        "junk",
    ]) == [{"name": "Test Dad", "phone": "+15550100101"}, {"name": "Test Mom", "phone": ""}]
    assert parent_contacts(None) == []


def test_extra_lock_apps_are_listed_once_and_never_the_safety_core():
    rules = {"lock_suspend_apps": ["com.example.watch", "com.example.watch", "", 7, *NEVER_BLOCKED_CORE]}
    assert lock_suspend_apps(rules) == ["com.example.watch"]
    assert lock_suspend_apps(None) == []


def test_the_status_carries_the_extra_lock_apps():
    s = evaluate({"lock_suspend_apps": ["com.example.watch"]}, used_ms=0, bonus_min=0, now=NOW)
    assert s["lock_suspend_apps"] == ["com.example.watch"]
    assert evaluate(None, used_ms=0, bonus_min=0, now=NOW)["lock_suspend_apps"] == []


BEDTIME = {"bedtime_start": "21:00", "bedtime_end": "07:00", "lock_suspend_apps": ["com.example.watch"]}


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 21, 21, 0, tzinfo=TZ),   # the first minute
        datetime(2026, 10, 21, 23, 59, tzinfo=TZ),  # before midnight
        datetime(2026, 10, 22, 0, 0, tzinfo=TZ),    # across midnight
        datetime(2026, 10, 22, 3, 30, tzinfo=TZ),
        datetime(2026, 10, 22, 6, 59, tzinfo=TZ),   # the last minute
    ],
)
def test_bedtime_is_a_full_lock_for_its_whole_window(now):
    """The phone's watch and call lock applies whenever the status is locked,
    so bedtime has to read locked (reason "bedtime") from start to end, with
    the extra lock apps still in the status."""
    s = evaluate(BEDTIME, used_ms=0, bonus_min=0, now=now)
    assert (s["locked"], s["reason"]) == (True, "bedtime")
    assert s["until"] == datetime(2026, 10, 22, 7, 0, tzinfo=TZ).isoformat()
    assert s["lock_suspend_apps"] == ["com.example.watch"]


@pytest.mark.parametrize("now", [datetime(2026, 10, 21, 20, 59, tzinfo=TZ), datetime(2026, 10, 22, 7, 0, tzinfo=TZ)])
def test_bedtime_lifts_at_its_edges(now):
    assert evaluate(BEDTIME, used_ms=0, bonus_min=0, now=now)["locked"] is False
