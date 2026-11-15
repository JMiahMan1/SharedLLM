"""The local model's app review: the request it gets and what is accepted back."""
import json

import pytest

from services.common import app_review

LISTING = {
    "title": "Block World", "developer": "Some Studio", "genre": "Adventure", "content_rating": "Teen",
    "content_descriptors": ["Fantasy Violence"], "interactive_elements": ["Users Interact"],
    "contains_ads": True, "in_app_purchases": True, "in_app_price_range": "$0.99 - $9.99 per item",
    "data_safety": {"lines": [{"heading": "This app may share these data types with third parties", "detail": "Location"}]},
    "score": 4.1, "ratings_count": 1000, "installs": "1,000,000+", "description": "Build worlds with friends.",
    "reviews": [
        {"stars": 1, "text": "Random people kept sending my son friend requests and messages.", "at": "2026-10-01"},
        {"stars": 2, "text": "There is an ad after   every single round.", "at": "2026-10-02"},
    ],
}


def _answer(**overrides):
    body = {"summary": "A building game with open chat.", "concerns": [
        {"category": "contact_strangers", "severity": "high", "why": "Strangers can message players.",
         "evidence": ["Random people kept sending my son friend requests"]},
    ]}
    body.update(overrides)
    return json.dumps(body)


def test_the_request_carries_the_listing_and_reviews_but_no_family_data():
    msgs = app_review.messages(LISTING)
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "Content rating: Teen" in user and "Users Interact" in user and "Random people" in user
    assert "Contains ads: yes" in user


def test_a_good_answer_is_read():
    out = app_review.parse("Here you go:\n" + _answer(), LISTING)
    assert out["summary"].startswith("A building game")
    assert out["concerns"][0]["category"] == "contact_strangers" and out["concerns"][0]["severity"] == "high"
    assert out["concerns"][0]["evidence"] == ["Random people kept sending my son friend requests"]
    assert out["dropped_quotes"] == 0


def test_an_empty_concern_list_is_a_clean_app():
    assert app_review.parse(_answer(concerns=[]), LISTING)["concerns"] == []


@pytest.mark.parametrize("content", [
    "I think it is fine.",
    "{not json}",
    "[1, 2]",
    json.dumps({"concerns": []}),
    json.dumps({"summary": "", "concerns": []}),
    json.dumps({"summary": "ok", "concerns": "none"}),
    json.dumps({"summary": "ok", "concerns": [{"category": "aliens", "severity": "high", "why": "x"}]}),
    json.dumps({"summary": "ok", "concerns": [{"category": "ads", "severity": "critical", "why": "x"}]}),
    json.dumps({"summary": "ok", "concerns": [{"category": "ads", "severity": "high", "why": ""}]}),
    json.dumps({"summary": "ok", "concerns": [{"category": "ads", "severity": "high", "why": "x", "evidence": "q"}]}),
    json.dumps({"summary": "ok", "concerns": ["ads"]}),
    json.dumps({"summary": "ok", "concerns": [{"category": "ads", "severity": "medium", "why": "x"}] * 9}),
])
def test_a_bad_answer_is_rejected_not_guessed(content):
    with pytest.raises(ValueError):
        app_review.parse(content, LISTING)


def test_a_quote_nobody_wrote_is_dropped_and_counted():
    answer = _answer(concerns=[{"category": "ads", "severity": "medium", "why": "Ads after rounds.",
                                "evidence": ["an ad after every single round", "the worst game ever made"]}])
    out = app_review.parse(answer, LISTING)
    assert out["concerns"][0]["evidence"] == ["an ad after every single round"]
    assert out["dropped_quotes"] == 1


def test_one_entry_per_category_keeps_the_more_serious():
    answer = _answer(concerns=[
        {"category": "ads", "severity": "medium", "why": "Some ads.", "evidence": []},
        {"category": "ads", "severity": "high", "why": "Constant ads.", "evidence": []},
    ])
    out = app_review.parse(answer, LISTING)
    assert len(out["concerns"]) == 1 and out["concerns"][0]["severity"] == "high"
    assert out["concerns"][0]["why"] == "Constant ads."
