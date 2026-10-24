"""Content monitoring: reviewed phrases, levels, and who gets paged."""

from services.common.concern_monitor import pages_parent, scan_event, scan_text


def _cats(text):
    return {hit["category"] for hit in scan_text(text)}


def test_self_harm_is_severe_with_context():
    [hit] = scan_text("honestly i just want to die after today")
    assert hit["category"] == "self_harm" and hit["level"] == "severe"
    assert "want to die" in hit["excerpt"]


def test_grooming_and_secrecy_patterns():
    assert "exploitation" in _cats("you can trust me, it's our little secret. don't tell your parents")
    assert "secrecy" in _cats("keep this between us ok")


def test_words_inside_other_words_do_not_match():
    assert _cats("I need to finish my skills test and the weedwhacker broke") == set()
    assert _cats("Therapist says I'm doing better") == set()  # "the rapist" is not a phrase here


def test_ordinary_messages_raise_nothing():
    assert _cats("Can you pick me up after practice? Love you") == set()
    assert _cats("Loving the new Lecrae album") == set()


def test_an_sms_event_is_scanned_body_and_sender():
    event = {"feature": "sms", "item": "Unknown", "action": "incoming", "at": "t",
             "detail": {"body": "everyone hates you, just kys"}}
    hits = scan_event(event)
    assert {h["category"] for h in hits} == {"bullying"}
    assert hits[0]["feature"] == "sms" and hits[0]["action"] == "incoming"


def test_titles_and_sites_are_scanned_too():
    assert {h["category"] for h in scan_event({"feature": "web", "item": "draftkings.com"})} == {"gambling"}


def test_who_gets_paged():
    severe = {"category": "violence", "level": "severe"}
    high = {"category": "bullying", "level": "high"}
    self_harm = {"category": "self_harm", "level": "severe"}
    assert pages_parent(severe, None) and not pages_parent(high, None)
    assert pages_parent(high, "all")
    assert not pages_parent(severe, "off") and pages_parent(self_harm, "off")
