"""Video calls: the shipped lists, a parent's choices, and what the phone is told."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.common import concern_review, video_calls
from services.common.screen_time import evaluate

NOW = datetime(2026, 11, 17, 16, 0, tzinfo=ZoneInfo("Etc/GMT+7"))


def test_the_defaults_come_from_the_data_file():
    d = video_calls.DEFAULTS
    assert "facetime.apple.com" in d["sites"] and "meet.google.com" in d["sites"]
    assert "com.whatsapp" in d["apps"] and "us.zoom.videomeetings" in d["apps"]
    assert d["labels"]["com.whatsapp"] == "WhatsApp"
    assert d["browsers"]["com.android.chrome"] == ["com.android.chrome:id/url_bar"]
    assert all(video_calls.DOMAIN.match(s) for s in d["sites"])


def test_a_broken_data_file_fails_at_start(tmp_path):
    bad = tmp_path / "video_calls.json"
    bad.write_text('{"apps": [], "sites": [], "browsers": {}}')
    with pytest.raises(ValueError):
        video_calls._load_defaults(bad)


@pytest.mark.parametrize("raw, site", [
    ("*.zoom.us", "zoom.us"),
    ("https://FaceTime.Apple.com/join#v=1", "facetime.apple.com"),
    ("meet.google.com.", "meet.google.com"),
    ("whereby.com:443/room", "whereby.com"),
])
def test_sites_are_compared_as_bare_domains(raw, site):
    assert video_calls.normalize_site(raw) == site


def test_scan_follows_photo_scanning_until_a_parent_chooses():
    assert video_calls.resolve({})["scan"] is True
    assert video_calls.resolve({"photo_scan": False})["scan"] is False
    assert video_calls.resolve({"photo_scan": False, "video_calls": {"scan": True}})["scan"] is True
    assert video_calls.resolve({"monitoring": "off", "video_calls": {"scan": True}})["scan"] is False


def test_defaults_for_interval_cap_and_corroboration():
    v = video_calls.resolve({})
    assert (v["interval_s"], v["max_frames"], v["corroborate_frames"], v["block"]) == (45, 20, 2, False)
    assert v["custom_apps"] is False and v["custom_sites"] is False


def test_clean_checks_every_field():
    assert video_calls.clean({"scan": True, "interval_s": 60, "max_frames": 10, "corroborate_frames": 3,
                              "block": True, "apps": ["com.example.chat", "com.example.chat"],
                              "sites": ["*.Example.com", "call.example.org"]}) == {
        "scan": True, "interval_s": 60, "max_frames": 10, "corroborate_frames": 3, "block": True,
        "apps": ["com.example.chat"], "sites": ["example.com", "call.example.org"],
    }
    for bad in (
        {"scan": "yes"}, {"interval_s": 5}, {"max_frames": 0}, {"corroborate_frames": 1},
        {"apps": ["not a package"]}, {"sites": ["not a domain"]}, {"block": 1}, [],
    ):
        with pytest.raises(ValueError):
            video_calls.clean(bad)


def test_a_parents_lists_replace_the_defaults():
    v = video_calls.resolve({"video_calls": {"apps": ["com.example.chat"], "sites": ["call.example.org"]}})
    assert v["apps"] == ["com.example.chat"] and v["sites"] == ["call.example.org"]
    assert v["custom_apps"] and v["custom_sites"]


def test_the_block_switch_blocks_every_app_and_site_only_while_on():
    off = evaluate({}, 0, 0, NOW)
    assert "com.whatsapp" not in off["blocked_apps"] and "facetime.apple.com" not in off["web_block"]
    on = evaluate({"video_calls": {"block": True}}, 0, 0, NOW)
    assert set(video_calls.DEFAULTS["apps"]) <= set(on["blocked_apps"])
    assert set(video_calls.DEFAULTS["sites"]) <= set(on["web_block"])
    assert on["video_calls"]["block"] is True and on["video_calls"]["interval_s"] == 45


def test_an_always_allowed_app_stays_open_under_the_block():
    rules = {"video_calls": {"block": True}, "always_allowed": ["com.whatsapp"]}
    assert "com.whatsapp" not in evaluate(rules, 0, 0, NOW)["blocked_apps"]


def test_the_call_label_names_the_app_and_site():
    assert video_calls.call_label({"app": "Chrome", "site": "facetime.apple.com"}) == "Chrome (facetime.apple.com)"
    assert video_calls.call_label({"package": "com.whatsapp"}) == "WhatsApp"
    assert video_calls.call_label({}) == "a video call"


def test_the_second_check_is_told_what_to_look_for_not_what_the_first_said():
    msgs = concern_review.media_verify_messages("sexual", "data:image/jpeg;base64,QUJD")
    system = msgs[0]["content"]
    assert "second reviewer" in system and "not sure" in system and "nudity" in system
    assert msgs[1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_the_second_checks_answer_must_be_a_clear_true_or_false():
    assert concern_review.parse_media_check('{"confirmed": true, "reason": "x"}') == {"confirmed": True, "reason": "x"}
    for bad in ("", "yes", '{"confirmed": "yes"}'):
        with pytest.raises(ValueError):
            concern_review.parse_media_check(bad)
