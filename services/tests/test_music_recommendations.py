from services.common.music_recommendations import all_lanes, recommend_music


def test_recommend_music_matches_artist_lane():
    rec = recommend_music([{"feature": "media", "item": "Drake - Hotline Bling", "detail": {"artist": "Drake", "app": "Spotify"}}])
    assert rec is not None
    assert rec["artist"] == "Lecrae"
    assert rec["lane"] == "hip hop"
    assert "Drake" in rec["matched_on"]


def test_recommend_music_matches_genre():
    rec = recommend_music([{"feature": "media", "item": "Some song", "detail": {"album": "rock ballad", "app": "YouTube"}}])
    assert rec is not None
    assert rec["artist"] == "Skillet"


def test_recommend_music_returns_none_for_unknown():
    rec = recommend_music([{"feature": "media", "item": "obscure track", "detail": {"artist": "nobody", "app": "x"}}])
    assert rec is None


def test_recommend_music_empty_input():
    assert recommend_music([]) is None
    assert recommend_music(None) is None


def test_all_lanes_non_empty():
    lanes = all_lanes()
    assert len(lanes) >= 10
    for lane in lanes:
        assert lane["artist"] and lane["reason"]
