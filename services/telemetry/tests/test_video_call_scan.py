"""Video call checks: a flag from a call's screenshot is kept for review, but a
parent hears only when two independent looks agree on enough frames of one
call. The owner's rule is that a false positive must never trigger anything."""
import json

import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import guardian
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
# A synthetic 1x1 PNG: no real image is ever used here.
PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
FLAGGED = (
    '{"concern": true, "category": "sexual", "level": "severe", '
    '"description": "Possible nudity", "reason": "skin visible"}'
)


def _kid(**video):
    return {
        "username": "kid1", "display_name": "Kid One", "parents": ["parent1"], "monitoring": "all",
        "photo_scan": True, "device_kinds": ["phone"],
        "video_calls": {"scan": True, "corroborate_frames": 2, **video},
    }


@pytest.fixture
def wired(monkeypatch):
    fake = FakeRedis()
    sent: list[dict] = []
    kids = {"kid1": _kid()}
    looks = {"first": FLAGGED, "second": '{"confirmed": true, "reason": "clearly shown"}', "calls": []}

    async def _children(_session):
        return kids

    async def _deliver(body):
        sent.append(body)
        return {"status": "SUCCESS"}

    async def _settings(_session):
        return {"vision_ocr_proxy_url": "http://vision.local", "vision_ocr_model": "vision-test"}

    async def _chat(_session, url, payload, timeout):
        second = "second reviewer" in payload["messages"][0]["content"]
        looks["calls"].append("second" if second else "first")
        answer = looks["second" if second else "first"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr("services.telemetry.llm.chat_content", _chat)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    with TestClient(app) as client:
        yield client, fake, sent, kids, looks


def _frame(client, index, call_id="call-a", site="facetime.apple.com"):
    return client.post(
        "/api/telemetry/media/scan",
        headers=HEADERS,
        json={
            "user": "kid1",
            "media_type": "video_call",
            "file_name": f"video-call-{index}.jpg",
            "image_b64": PNG_1X1,
            "metadata": {
                "feature": "video_call", "app": "Chrome", "package": "com.android.chrome", "site": site,
                "call_id": call_id, "call_started_at": "2026-11-17T15:41:00-07:00", "frame_index": index,
            },
        },
    )


def _concerns(client):
    return client.get("/api/telemetry/concerns/kid1", headers=HEADERS).json()["concerns"]


def test_one_flagged_frame_notifies_nobody_and_waits_in_the_review_queue(wired):
    client, _fake, sent, _kids, looks = wired
    resp = _frame(client, 0)
    assert resp.status_code == 200 and resp.json()["concern"] is True
    assert resp.json()["alert"] == "unsure"
    assert sent == []
    assert looks["calls"] == ["first", "second"]
    [concern] = _concerns(client)
    assert concern["feature"] == "video_call"
    assert concern["alert"] == {"state": "unsure", "confirmed_frames": 1, "needed": 2}
    assert concern["review"]["verdict"] == "unsure"
    assert concern["video_call"]["site"] == "facetime.apple.com"
    assert concern["video_call"]["frame_index"] == 0
    assert concern["item"] == "Chrome (facetime.apple.com)"
    assert concern["has_image"] is True  # kept exactly like a flagged photo


def test_when_the_second_check_disagrees_nobody_is_notified(wired):
    client, _fake, sent, _kids, looks = wired
    looks["second"] = '{"confirmed": false, "reason": "ordinary clothing"}'
    for i in range(4):
        _frame(client, i)
    assert sent == []
    concerns = _concerns(client)
    assert len(concerns) == 4
    assert all(c["alert"]["state"] == "unsure" and c["alert"]["confirmed_frames"] == 0 for c in concerns)
    assert all(c["review"]["second_check"]["confirmed"] is False for c in concerns)


def test_a_second_check_that_cannot_answer_counts_as_not_confirmed(wired):
    client, _fake, sent, _kids, looks = wired
    looks["second"] = RuntimeError("vision server down")
    _frame(client, 0)
    _frame(client, 1)
    assert sent == []
    assert all("unavailable" in c["review"]["second_check"]["reason"] for c in _concerns(client))


def test_two_corroborated_frames_send_one_notification_not_two(wired):
    client, _fake, sent, _kids, _looks = wired
    _frame(client, 0)
    assert sent == []
    _frame(client, 1)
    assert len(sent) == 1
    notice = sent[0]
    assert notice["user"] == "parent1" and notice["audience"] == "parents"
    assert "video call" in notice["title"]
    assert "Chrome (facetime.apple.com)" in notice["body"]
    assert notice["data"]["feature"] == "video_call"
    # More frames of the same call and category never page again.
    _frame(client, 2)
    _frame(client, 3)
    assert len(sent) == 1
    states = [c["alert"]["state"] for c in _concerns(client)]  # newest first
    assert states == ["already_sent", "already_sent", "sent", "unsure"]


def test_a_retried_upload_of_the_same_frame_is_not_a_second_frame(wired):
    client, _fake, sent, _kids, _looks = wired
    _frame(client, 0)
    _frame(client, 0)
    assert sent == []


def test_frames_from_different_calls_do_not_add_up(wired):
    client, _fake, sent, _kids, _looks = wired
    _frame(client, 0, call_id="call-a")
    _frame(client, 0, call_id="call-b")
    assert sent == []


def test_the_corroboration_count_is_a_setting(wired):
    client, _fake, sent, kids, _looks = wired
    kids["kid1"] = _kid(corroborate_frames=3)
    _frame(client, 0)
    _frame(client, 1)
    assert sent == []
    _frame(client, 2)
    assert len(sent) == 1


def test_a_setting_below_two_still_needs_two_frames(wired):
    client, _fake, sent, kids, _looks = wired
    kids["kid1"] = _kid(corroborate_frames=1)
    _frame(client, 0)
    assert sent == []


def test_a_clean_frame_leaves_nothing(wired):
    client, _fake, sent, _kids, looks = wired
    looks["first"] = '{"concern": false, "reason": "clean"}'
    assert _frame(client, 0).json() == {"status": "SUCCESS", "concern": False}
    assert looks["calls"] == ["first"] and sent == [] and _concerns(client) == []


@pytest.mark.parametrize("child", [
    {"monitoring": "off"},
    {"video_calls": {"scan": False, "corroborate_frames": 2}},
])
def test_switched_off_means_not_scanned(wired, child):
    client, _fake, sent, kids, looks = wired
    kids["kid1"] = {**_kid(), **child}
    assert _frame(client, 0).json()["concern"] is False
    assert looks["calls"] == [] and sent == []


def test_video_calls_follow_their_own_switch_not_photo_scanning(wired):
    client, _fake, _sent, kids, _looks = wired
    kids["kid1"] = {**_kid(), "photo_scan": False}
    assert _frame(client, 0).json()["concern"] is True


def test_a_flag_never_changes_the_childs_settings_or_blocks_anything(wired):
    client, fake, _sent, _kids, _looks = wired
    before = {*fake.strings, *fake.sets, *fake.lists, *fake.hashes, *fake.zsets}
    _frame(client, 0)
    _frame(client, 1)
    # Only the concern list, its kept images and the call's tally and told
    # marks are written: no rules, no blocked apps, no lock.
    written = {*fake.strings, *fake.sets, *fake.lists, *fake.hashes, *fake.zsets} - before
    assert written
    for key in written:
        assert key.startswith(("tel:concerns:", "tel:concern-image:", "tel:video_call:")), key
    for concern in _concerns(client):
        assert "block" not in json.dumps(concern)


def test_a_first_look_that_cannot_answer_is_quietly_skipped_for_a_live_call(wired):
    client, _fake, sent, _kids, looks = wired
    looks["first"] = TimeoutError()
    resp = _frame(client, 0)
    assert resp.status_code == 200 and resp.json()["concern"] is False
    assert sent == [] and _concerns(client) == []
