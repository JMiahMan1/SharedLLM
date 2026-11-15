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
    medium = {"category": "profanity", "level": "medium"}
    # The default is high and severe.
    assert pages_parent(severe, None) and pages_parent(high, None) and not pages_parent(medium, None)
    assert pages_parent(medium, "all") and pages_parent(high, "all")
    assert pages_parent(high, "high") and not pages_parent(medium, "high")
    assert pages_parent(severe, "severe") and not pages_parent(high, "severe")
    assert not pages_parent(severe, "off") and pages_parent(self_harm, "off")


def test_each_parent_chooses_their_own_level():
    from services.common.concern_monitor import parent_level

    child = {"monitoring": "severe", "notify_levels": {"mom": "all", "dad": "bogus"}}
    assert parent_level(child, "mom") == "all"
    assert parent_level(child, "dad") == "severe"  # an unknown value falls back to the child's default
    assert parent_level({}, "dad") == "high"


def test_a_hit_carries_wider_context_and_its_source_item():
    body = ("before " * 40) + "i want to die" + (" after" * 40)
    [hit] = scan_event({"feature": "sms", "item": "Unknown", "action": "incoming", "detail": {"body": body}})
    assert hit["item"] == "Unknown"
    assert "want to die" in hit["excerpt"] and "want to die" in hit["context"]
    assert len(hit["context"]) > len(hit["excerpt"])
    assert hit["context"].startswith("…") and hit["context"].endswith("…")
