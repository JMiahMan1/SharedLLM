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


# --- the message a picture or voice note came in ---

MMS_META = {
    "source": "mms",
    "direction": "in",
    "contact": "Alex",
    "number": "+15550001",
    "message_text": "look at this",
    "sent_at": 1_760_000_000_000,
    "message_key": "mms:12",
}


def test_the_context_keeps_who_said_it_and_what_they_said():
    context = concern_review.message_context(MMS_META)
    assert context["contact"] == "Alex" and context["direction"] == "in"
    assert context["source"] == "mms" and context["message_text"] == "look at this"
    assert context["sent_at"] == 1_760_000_000_000
    # The dedupe key belongs to the phone's retry, not to what a parent reads.
    assert "message_key" not in context


def test_no_context_is_not_invented():
    assert concern_review.message_context(None) is None
    assert concern_review.message_context({}) is None
    assert concern_review.message_context({"number": "  ", "direction": "sideways"}) is None


def test_a_group_names_its_members_without_the_blanks():
    context = concern_review.message_context({**MMS_META, "group": ["Alex", "Sam", " "]})
    assert context["group"] == ["Alex", "Sam"]
    assert "in a group with Alex, Sam" in concern_review.message_summary(context)


def test_the_summary_reads_the_same_everywhere():
    context = concern_review.message_context(MMS_META)
    assert concern_review.message_summary(context) == 'From Alex (MMS) · Message: "look at this"'
    assert concern_review.message_summary(context, with_text=False) == "From Alex (MMS)"
    sent = concern_review.message_context({**MMS_META, "direction": "out"})
    assert concern_review.message_summary(sent, with_text=False) == "Sent to Alex (MMS)"
    # Nothing to say is said as nothing, not as "From an unknown number".
    assert concern_review.message_summary(None) == ""


def test_a_name_that_already_says_the_kind_is_not_repeated():
    context = concern_review.message_context({"source": "mms", "contact": "Alex (MMS)"})
    assert concern_review.message_summary(context) == "From Alex (MMS)"


def test_the_vision_prompt_says_who_sent_the_picture():
    msgs = concern_review.media_messages("image", "photo.jpg", "AAAA", MMS_META)
    text = msgs[1]["content"][0]["text"]
    assert "From Alex (MMS)" in text and "look at this" in text
    # The image is still the second part, unchanged by the added words.
    assert msgs[1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"


def test_a_picture_without_context_reads_as_before():
    text = concern_review.media_messages("image", "photo.jpg", "AAAA")[1]["content"][0]["text"]
    assert text.startswith("Scanned from image file: 'photo.jpg'.")
    assert "From" not in text


def test_a_voice_message_is_named_as_one_and_carries_who_spoke():
    concern = {
        **scan_text("i want to die")[0],
        "feature": "sms",
        "action": "voice_message",
        "message_context": concern_review.message_context(
            {"source": "mms", "direction": "in", "contact": "Leo", "message_text": "listen to this"}
        ),
    }
    text = concern_review.messages(concern, "i want to die")[1]["content"]
    assert "a recorded voice message in a text conversation" in text
    assert "From Leo (MMS)." in text
    assert 'The message with it said: "listen to this"' in text


def test_a_voice_message_with_no_context_says_nothing_about_one():
    concern = {**scan_text("i want to die")[0], "feature": "sms", "action": "voice_message"}
    text = concern_review.messages(concern, "i want to die")[1]["content"]
    assert "a recorded voice message in a text conversation" in text
    assert "The message with it said" not in text
