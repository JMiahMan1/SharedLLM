"""Tier-1 Drop In FastPath: unambiguous commands only."""
from __future__ import annotations

import pytest

from services.gateway.fast_drop_in import parse_drop_in_command


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ("drop in on the kitchen", "kitchen"),
        ("drop in to the den", "den"),
        ("drop into the office", "office"),
        ("drop in kitchen", "kitchen"),
        ("intercom the living room", "living room"),
        ("Intercom office.", "office"),
        ("  drop   in   on   the   kitchen.  ", "kitchen"),
        ("drop in on room_of:alex", "room_of:alex"),
    ],
)
def test_start_phrases(query: str, target: str):
    assert parse_drop_in_command(query) == {
        "tool": "DropInRequest",
        "payload": {"action": "start", "target_room": target},
    }


@pytest.mark.parametrize(
    "query",
    [
        "hang up",
        "hang up the phone",
        "end drop in",
        "end the drop in",
        "stop drop in",
        "stop the drop-in",
        "end the call",
        "end this call",
        "Hang up!",
    ],
)
def test_end_phrases(query: str):
    assert parse_drop_in_command(query) == {
        "tool": "DropInRequest",
        "payload": {"action": "end"},
    }


@pytest.mark.parametrize(
    "query",
    [
        "drop in the laundry",
        "announce dinner is ready",
        "tell the kitchen dinner is ready",
        "intercom the kitchen dinner is ready",
        "intercom the kitchen and say dinner is ready",
        "drop in on the kitchen and tell them dinner is ready",
        "drop in on the kitchen to say dinner is ready",
        "drop in",
        "drop in on",
        "intercom",
        "turn off the kitchen lights",
        "what is the weather",
        "",
        "   ",
    ],
)
def test_non_drop_in_phrases_are_left_to_the_model(query: str):
    assert parse_drop_in_command(query) is None
