"""Firmware is offered (and installed by the nightly Echo rollout) only when it is newer."""
from __future__ import annotations

import pytest

from services.homehub.core import UNKNOWN, is_newer_version, update_state


@pytest.mark.parametrize(("latest", "installed", "newer"), [
    ("2026.11.18", "2026.10.17", True),
    ("2026.10.17", "2026.11.18", False),  # 2026-11-25: an Echo on 2026.11.18 was offered 2026.10.17
    ("2026.11.18", "2026.11.18", False),
    ("1.5.20", "1.5.3", True),            # numbers, not text: "1.5.20" > "1.5.3"
    ("1.5.3", "1.5.20", False),
    ("2.0", "1.9.9", True),
    ("1.10", "1.10.0", False),            # same version, written two ways
    ("v1.7.1", "1.7.0", True),
    ("2024.12.1-dev", "2024.12.0", True),
    ("nightly-abc", "nightly-def", True),  # unreadable: different still counts, as before
    (None, "1.0", False),
    ("1.0", None, False),
])
def test_is_newer_version(latest, installed, newer):
    assert is_newer_version(latest, installed) is newer


def test_an_older_version_is_not_an_update():
    attrs = {"latest_version": "2026.10.17", "installed_version": "2026.11.18"}
    assert update_state("on", attrs, None) == "off"


def test_a_newer_version_is_an_update_unless_skipped():
    attrs = {"latest_version": "2026.11.18", "installed_version": "2026.10.17"}
    assert update_state("off", dict(attrs), None) == "on"
    assert update_state("off", dict(attrs), "2026.11.18") == "off"


def test_unknown_state_stands():
    attrs = {"latest_version": "2026.11.18", "installed_version": "2026.10.17"}
    assert update_state(UNKNOWN, attrs, None) == UNKNOWN
