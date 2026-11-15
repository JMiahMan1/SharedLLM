"""Reading public Play listings, on saved (trimmed) pages of well-known apps."""
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
