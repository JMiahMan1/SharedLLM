"""The warn-once helper that keeps a repeated failure visible but not noisy."""

import logging

import pytest

from services.common import logging_utils
from services.common.logging_utils import warn_periodically


@pytest.fixture(autouse=True)
def _fresh_state():
    logging_utils._last_warned.clear()
    yield
    logging_utils._last_warned.clear()


def test_the_first_failure_warns(caplog):
    with caplog.at_level(logging.DEBUG):
        warn_periodically(logging.getLogger("t"), "k", "boom %s", 1)
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert caplog.records[0].getMessage() == "boom 1"


def test_a_repeat_inside_the_cooldown_drops_to_debug(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.DEBUG):
        warn_periodically(log, "k", "boom")
        warn_periodically(log, "k", "boom")
    assert [record.levelname for record in caplog.records] == ["WARNING", "DEBUG"]


def test_it_warns_again_once_the_cooldown_expires(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.DEBUG):
        warn_periodically(log, "k", "boom", cooldown_s=0)
        warn_periodically(log, "k", "boom", cooldown_s=0)
    assert [record.levelname for record in caplog.records] == ["WARNING", "WARNING"]


def test_two_failures_do_not_silence_each_other(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.DEBUG):
        warn_periodically(log, "a", "first")
        warn_periodically(log, "b", "second")
    assert [record.levelname for record in caplog.records] == ["WARNING", "WARNING"]
