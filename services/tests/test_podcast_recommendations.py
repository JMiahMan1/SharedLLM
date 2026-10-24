from services.common.podcast_recommendations import recommend_podcast, all_themes


def test_recommend_podcast_matches_show_theme():
    rec = recommend_podcast([{"feature": "media", "item": "Joe Rogan Experience #2000", "detail": {"show": "The Joe Rogan Experience", "app": "Spotify"}}])
    assert rec is not None
    assert rec["show"] == "That Sounds Fun"
    assert rec["theme"] == "culture conversation"
    assert "Joe Rogan" in rec["matched_on"]
    assert rec["redeemed"] is True


def test_recommend_podcast_matches_topic():
    rec = recommend_podcast([{"feature": "media", "item": "Daily news update", "detail": {"genre": "news", "app": "Apple Podcasts"}}])
    assert rec is not None
    assert rec["show"] == "The World and Everything in It"
    assert rec["theme"] == "worldview news"


def test_recommend_podcast_redirects_when_not_redeemed():
    rec = recommend_podcast([{"feature": "media", "item": "Serial Episode 1", "detail": {"show": "Serial", "genre": "true crime"}}])
    assert rec is not None
    assert rec["redeemed"] is False
    assert rec["show"] is None
    assert "audio drama" in (rec.get("redirect_to") or "")


def test_recommend_podcast_returns_none_for_unknown():
    rec = recommend_podcast([{"feature": "media", "item": "unknown obscure audio", "detail": {"show": "nobody", "app": "x"}}])
    assert rec is None


def test_recommend_podcast_empty_input():
    assert recommend_podcast([]) is None
    assert recommend_podcast(None) is None


def test_all_themes_non_empty():
    themes = all_themes()
    assert len(themes) >= 8
    for theme in themes:
        assert theme["reason"]


def test_longer_triggers_win_over_shorter_ones():
    rec = recommend_podcast([{"feature": "podcast", "item": "A family audio drama"}])
    assert rec["theme"] == "kids drama"


def test_triggers_match_whole_words_only():
    assert recommend_podcast([{"feature": "podcast", "item": "Newsboys live"}]) is None


def test_only_podcast_listening_reaches_the_recommender():
    from services.common.podcast_recommendations import podcast_events

    events = [
        {"feature": "media", "item": "Crime Junkie", "detail": {"app": "com.spotify.music"}},
        {"feature": "media", "item": "Crime Junkie", "detail": {"app": "au.com.shiftyjelly.pocketcasts"}},
        {"feature": "media", "item": "Ep. 12", "detail": {"show": "Crime Junkie", "app": "com.spotify.music"}},
        {"feature": "podcast", "item": "Crime Junkie"},
        {"feature": "music", "item": "Crime Junkie"},
    ]
    assert podcast_events(events) == events[1:4]
