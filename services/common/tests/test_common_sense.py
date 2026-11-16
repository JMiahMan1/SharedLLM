"""Reading a Common Sense Media app review page (a made-up page in the site's
markup), finding the page by the app's name, and refusing half a review."""
from pathlib import Path

import pytest

from services.common import common_sense

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "common_sense"
URL = "https://www.commonsensemedia.org/app-reviews/rail-dash-runner"


def _page():
    return (FIXTURES / "rail-dash-runner.html").read_text(encoding="utf-8")


def test_the_age_the_summary_and_both_consensus_are_read():
    review = common_sense.parse(_page(), URL)
    assert review["name"] == "Rail Dash Runner" and review["kind"] == "app"
    assert review["age"] == 9 and review["age_label"] == "age 9+"
    assert review["quality_stars"] == 4 and review["published"] == "2021-04-02"
    # The whole paragraph, not the teaser, with the markup gone.
    assert review["parents_need_to_know"] == (
        "Parents need to know that Rail Dash Runner is an endless runner with cartoon crashes. Coins can be bought "
        "with real money, but the game never pushes it. Players can share scores with friends, which is optional.")
    assert review["parents_say"] == {"age": 10, "reviews": 12, "summary": (
        "Parents say the game is fun and simple, though some worry about how often it asks for purchases.")}
    assert review["kids_say"] == {"age": 8, "reviews": 1030, "summary": "Kids say it is fast and fun but gets repetitive."}


def test_no_reviewer_or_critic_name_is_kept():
    review = common_sense.parse(_page(), URL)
    flat = repr(review)
    assert "Pat Critic" not in flat and "Made-up Parent" not in flat


def test_a_page_without_review_data_is_an_error():
    with pytest.raises(common_sense.CommonSenseError, match="no app or game review data"):
        common_sense.parse("<html><title>Parents' Ultimate Guide</title><p>Parents need to know.</p></html>", URL)


def test_a_review_without_parents_need_to_know_is_an_error_not_half_a_review():
    page = _page().replace("<p>Parents need to know that", "<p>Some text that")
    with pytest.raises(common_sense.CommonSenseError, match="Parents need to know"):
        common_sense.parse(page, URL)


def test_a_page_whose_shown_age_disagrees_with_its_data_is_an_error():
    page = _page().replace('"typicalAgeRange": "9+"', '"typicalAgeRange": "13+"')
    with pytest.raises(common_sense.CommonSenseError, match="shows age 9"):
        common_sense.parse(page, URL)


def test_a_review_without_an_age_is_an_error():
    page = _page().replace('"typicalAgeRange": "9+",', "")
    with pytest.raises(common_sense.CommonSenseError, match="no age rating"):
        common_sense.parse(page, URL)


def test_a_page_with_no_user_reviews_has_no_consensus():
    page = _page().split('<div class="tab__content">')[0] + "</body></html>"
    review = common_sense.parse(page, URL)
    assert review["parents_say"] is None and review["kids_say"] is None


def test_the_pages_to_try_come_from_the_name_alone():
    assert common_sense.candidate_urls("Rail Dash Runner") == [
        "https://www.commonsensemedia.org/app-reviews/rail-dash-runner",
        "https://www.commonsensemedia.org/game-reviews/rail-dash-runner",
    ]
    urls = common_sense.candidate_urls("Word Garden: Spell & Learn")
    assert urls == [
        "https://www.commonsensemedia.org/app-reviews/word-garden-spell-and-learn",
        "https://www.commonsensemedia.org/game-reviews/word-garden-spell-and-learn",
        "https://www.commonsensemedia.org/app-reviews/word-garden",
        "https://www.commonsensemedia.org/game-reviews/word-garden",
    ]
    assert len(common_sense.candidate_urls("A: B - C | D (E)")) <= common_sense.MAX_CANDIDATES
    assert common_sense.candidate_urls("!!!") == []


def test_only_redirects_to_another_review_are_followed():
    assert common_sense.redirect_target("/app-reviews/word-garden") == \
        "https://www.commonsensemedia.org/app-reviews/word-garden"
    assert common_sense.redirect_target("https://www.commonsensemedia.org/game-reviews/rail-dash") == \
        "https://www.commonsensemedia.org/game-reviews/rail-dash"
    assert common_sense.redirect_target("/articles/parents-ultimate-guide-to-something") is None
    assert common_sense.redirect_target("/website-reviews/word-garden") is None
    assert common_sense.redirect_target("https://elsewhere.example/app-reviews/word-garden") is None
    assert common_sense.redirect_target(None) is None


def test_a_page_must_review_this_app():
    assert common_sense.same_app("Word Garden", "Word Garden: Spell & Learn")
    assert common_sense.same_app("Rail Dash Runner", "rail dash runner")
    assert not common_sense.same_app("Rail Dash", "Garden Dash")
    assert not common_sense.same_app("", "Rail Dash")


def test_the_request_carries_only_generic_headers():
    assert set(common_sense.REQUEST_HEADERS) == {"User-Agent", "Accept-Language"}
