"""Reading public Play listings, on saved (trimmed) pages of well-known apps."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from services.common import play_listing

FIXTURES = Path(__file__).parent / "fixtures" / "play"


def _page(package: str) -> str:
    return (FIXTURES / f"{package}.html").read_text(encoding="utf-8")


def test_a_game_with_chat_and_loot_boxes():
    listing = play_listing.parse(_page("com.roblox.client"), "com.roblox.client")
    assert listing["title"] == "Roblox" and listing["developer"] == "Roblox Corporation"
    assert listing["content_rating"] == "Teen"
    assert listing["interactive_elements"] == ["Users Interact", "In-Game Purchases (Includes Random Items)"]
    assert listing["content_descriptors"] == ["Diverse Content: Discretion Advised"]
    assert listing["in_app_purchases"] is True and listing["in_app_price_range"].startswith("$")
    assert listing["contains_ads"] is False
    safety = listing["data_safety"]
    assert safety["shares_data"] is True and "Location" in safety["shared_types"]
    assert safety["collects_data"] is True and safety["encrypted_in_transit"] is True


def test_an_app_with_ads():
    listing = play_listing.parse(_page("com.kiloo.subwaysurf"), "com.kiloo.subwaysurf")
    assert listing["contains_ads"] is True
    assert listing["content_rating"] == "Everyone 10+"
    assert listing["content_descriptors"] == ["Fantasy Violence"]


def test_an_app_that_shares_nothing():
    listing = play_listing.parse(_page("org.khanacademy.android"), "org.khanacademy.android")
    assert listing["data_safety"]["shares_data"] is False
    assert listing["interactive_elements"] == [] and listing["in_app_purchases"] is False


def test_reviews_keep_the_words_and_stars_never_the_reviewer():
    listing = play_listing.parse(_page("com.roblox.client"), "com.roblox.client")
    assert 0 < len(listing["reviews"]) <= play_listing.MAX_REVIEWS
    review = listing["reviews"][0]
    assert set(review) == {"stars", "text", "at", "thumbs_up", "version"}
    assert 1 <= review["stars"] <= 5 and review["text"]


def test_the_page_of_another_package_is_refused():
    with pytest.raises(play_listing.ListingError):
        play_listing.parse(_page("com.roblox.client"), "com.kiloo.subwaysurf")


@pytest.mark.parametrize("page", ["", "<html>no data here</html>", "<html>Sign in to continue</html>"])
def test_a_page_without_listing_data_is_an_error_not_half_a_listing(page):
    with pytest.raises(play_listing.ListingError):
        play_listing.parse(page, "com.roblox.client")


def test_the_hash_moves_with_what_the_review_reads():
    listing = play_listing.parse(_page("com.roblox.client"), "com.roblox.client")
    again = play_listing.parse(_page("com.roblox.client"), "com.roblox.client")
    assert listing["hash"] == again["hash"]
    changed = {**listing, "content_rating": "Mature 17+"}
    assert play_listing.listing_hash(changed) != listing["hash"]
    fewer = {**listing, "reviews": listing["reviews"][1:]}
    assert play_listing.listing_hash(fewer) != listing["hash"]


def test_only_the_package_name_is_in_the_request():
    url = play_listing.listing_url("com.roblox.client")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "play.google.com", "/store/apps/details")
    assert parse_qs(parsed.query) == {"id": ["com.roblox.client"], "hl": ["en_US"], "gl": ["US"]}
    assert set(play_listing.REQUEST_HEADERS) == {"User-Agent", "Accept-Language"}
    assert "jarvis" not in str(play_listing.REQUEST_HEADERS).lower()


def test_a_card_summary_leaves_out_the_description_and_reviews():
    listing = play_listing.parse(_page("com.kiloo.subwaysurf"), "com.kiloo.subwaysurf")
    summary = play_listing.summary(listing)
    assert "reviews" not in summary and "description" not in summary and summary["title"] == "Subway Surfers"


# --- more reviews: the newest and the lowest rated ---------------------------


def _row(stars, text, seconds=1_794_000_000, name="Made-up Reviewer"):
    """One review row in Play's shape (made-up values; the name and picture are where Play puts them)."""
    return ["id-" + text[:8], [name, [None, 2, None, [None, None, "https://example.invalid/pic.png"]]], stars, None,
            text, [seconds, 0], 3, None, None, None, "1.2.3", None, None, None, None, None, None]


def _rpc(rows):
    """A batchexecute answer as Play sends it: an anti-JSON prefix, then the envelope."""
    import json as _json

    inner = _json.dumps([rows, [None, "next-page-token"]])
    return ")]}'\n\n" + _json.dumps([["wrb.fr", "UsvDTd", inner, None, None, None, "generic"], ["di", 120]]) + "\n25\n"


def test_the_reviews_request_carries_the_package_sort_and_stars_only():
    url, form, headers = play_listing.reviews_request("com.example.game", "one_star")
    assert url.startswith(play_listing.REVIEWS_URL)
    assert parse_qs(urlparse(url).query) == {"rpcids": ["UsvDTd"], "hl": ["en_US"], "gl": ["US"]}
    outer = json.loads(form["f.req"])
    assert outer[0][0][0] == "UsvDTd"
    inner = json.loads(outer[0][0][1])
    assert inner == [None, None, [2, 2, [40, None, None], None, [None, 1]], ["com.example.game", 7]]
    assert set(headers) == {"User-Agent", "Accept-Language", "Content-Type"}
    newest = json.loads(json.loads(play_listing.reviews_request("com.example.game", "newest")[1]["f.req"])[0][0][1])
    assert newest[2][4] == [None, None]


def test_an_answer_of_reviews_keeps_words_stars_and_date_never_the_reviewer():
    reviews = play_listing.parse_reviews(_rpc([_row(1, "Strangers message kids here."), _row(1, "Too many ads.")]),
                                         "one_star")
    assert [r["text"] for r in reviews] == ["Strangers message kids here.", "Too many ads."]
    assert reviews[0] == {"stars": 1, "text": "Strangers message kids here.", "at": "2026-11-06", "thumbs_up": 3,
                          "version": "1.2.3"}
    assert "Made-up Reviewer" not in repr(reviews) and "pic.png" not in repr(reviews)


def test_an_app_with_no_such_reviews_is_an_empty_list():
    import json as _json

    empty = ")]}'\n\n" + _json.dumps([["wrb.fr", "UsvDTd", _json.dumps([None, None]), None, None, None, "generic"]])
    assert play_listing.parse_reviews(empty, "two_star") == []


@pytest.mark.parametrize("body", [
    "", "<html>consent page</html>", ")]}'\n\n[[\"wrb.fr\",\"UsvDTd\",null,null,null,[3],\"generic\"]]",
    ")]}'\n\n[[\"wrb.fr\",\"Other\",\"[]\",null]]",
])
def test_an_unreadable_reviews_answer_is_an_error(body):
    with pytest.raises(play_listing.ListingError):
        play_listing.parse_reviews(body, "newest")


def test_a_star_filtered_answer_with_other_stars_is_refused():
    with pytest.raises(play_listing.ListingError, match="1-star"):
        play_listing.parse_reviews(_rpc([_row(1, "Bad."), _row(5, "Great.")]), "one_star")


def test_the_extra_reviews_hash_moves_with_their_words():
    a = {"newest": {"reviews": [{"text": "one"}]}, "one_star": {"reviews": []}, "two_star": {"reviews": []}}
    b = {"newest": {"reviews": [{"text": "two"}]}, "one_star": {"reviews": []}, "two_star": {"reviews": []}}
    assert play_listing.more_reviews_hash(a) != play_listing.more_reviews_hash(b)
    assert play_listing.more_reviews_hash(a) == play_listing.more_reviews_hash(dict(a))
