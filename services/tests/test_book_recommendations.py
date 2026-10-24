from services.common.book_recommendations import recommend_book, all_themes


def test_recommend_book_matches_author_theme():
    rec = recommend_book([{"feature": "reading", "item": "The Hobbit", "detail": {"author": "J.R.R. Tolkien", "app": "Kindle"}}])
    assert rec is not None
    assert rec["title"] == "The Wingfeather Saga"
    assert rec["author"] == "Andrew Peterson"
    assert rec["theme"] == "epic fantasy"
    assert "Tolkien" in rec["matched_on"]
    assert rec["redeemed"] is True


def test_recommend_book_matches_topic():
    rec = recommend_book([{"feature": "reading", "item": "Classical Apologetics", "detail": {"genre": "theology", "app": "Apple Books"}}])
    assert rec is not None
    assert rec["title"] == "Mere Christianity"
    assert rec["theme"] == "classic theology"


def test_recommend_book_redirects_when_not_redeemed():
    rec = recommend_book([{"feature": "reading", "item": "The Shining", "detail": {"author": "Stephen King", "genre": "horror"}}])
    assert rec is not None
    assert rec["redeemed"] is False
    assert rec["title"] is None
    assert "Psalms" in (rec.get("redirect_to") or "")


def test_recommend_book_returns_none_for_unknown():
    rec = recommend_book([{"feature": "reading", "item": "unknown obscure text", "detail": {"author": "nobody", "app": "x"}}])
    assert rec is None


def test_recommend_book_empty_input():
    assert recommend_book([]) is None
    assert recommend_book(None) is None


def test_all_themes_non_empty():
    themes = all_themes()
    assert len(themes) >= 8
    for theme in themes:
        assert theme["reason"]


def test_longer_triggers_win_over_shorter_ones():
    rec = recommend_book([{"feature": "reading", "item": "An epic fantasy saga"}])
    assert rec["theme"] == "epic fantasy"


def test_only_reading_and_audiobooks_reach_the_recommender():
    from services.common.book_recommendations import book_events

    events = [
        {"feature": "media", "item": "Horror Movie Theme", "detail": {"app": "com.google.android.youtube"}},
        {"feature": "audiobook", "item": "The Hobbit"},
        {"feature": "media", "item": "Dune", "detail": {"app": "com.audible.application"}},
        {"feature": "reading", "item": "Mere Christianity"},
    ]
    assert book_events(events) == events[1:]
