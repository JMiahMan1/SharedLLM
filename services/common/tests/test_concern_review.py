"""The local model's second look at a phrase-list hit: reading its answer safely."""
import pytest

from services.common import concern_review
from services.common.concern_monitor import pages_parent, scan_text

BULLY = {**scan_text("nobody likes you")[0], "feature": "sms", "action": "incoming"}
HARM = {**scan_text("i want to die")[0], "feature": "sms", "action": "outgoing"}


def test_the_request_names_the_category_and_carries_the_text():
    msgs = concern_review.messages(BULLY, "nobody likes you, just leave")
    assert msgs[0]["role"] == "system" and "JSON only" in msgs[0]["content"]
    assert "Bullying" in msgs[1]["content"] and "a text message" in msgs[1]["content"]
    assert "nobody likes you, just leave" in msgs[1]["content"]


def test_a_fenced_or_chatty_answer_is_read():
    v = concern_review.parse_verdict('Sure!\n```json\n{"concern": true, "level": "severe", "reason": "Targeted."}\n```')
    assert v == {"concern": True, "level": "severe", "reason": "Targeted."}
    assert concern_review.parse_verdict('{"concern": false, "level": "huge"}')["level"] is None


@pytest.mark.parametrize("bad", ["", "no idea", '{"concern": "maybe"}', "{not json}"])
def test_nonsense_is_refused_so_the_phrase_list_decides(bad):
    with pytest.raises(ValueError):
        concern_review.parse_verdict(bad)


def test_a_dismissed_hit_pages_no_one_but_self_harm_is_only_doubted():
    dismissed = concern_review.apply(BULLY, {"concern": False, "level": None, "reason": "a lyric"}, "m")
    assert dismissed["review"]["verdict"] == "dismissed" and not pages_parent(dismissed, "all")
    doubted = concern_review.apply(HARM, {"concern": False, "level": None, "reason": "a joke?"}, "m")
    assert doubted["review"]["verdict"] == "doubted" and pages_parent(doubted, "off")


def test_a_confirmed_hit_takes_the_models_level_and_keeps_the_phrase_level():
    confirmed = concern_review.apply(BULLY, {"concern": True, "level": "severe", "reason": "repeated"}, "m")
    assert confirmed["level"] == "severe" and confirmed["review"]["phrase_level"] == "high"
    assert pages_parent(confirmed, "severe")


def test_phrase_only_keeps_the_reason():
    assert concern_review.phrase_only(BULLY, "no model")["review"] == {"by": "phrases", "reason": "no model"}
