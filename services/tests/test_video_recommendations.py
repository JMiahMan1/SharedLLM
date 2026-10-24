from services.common.video_recommendations import all_themes, recommend_video


def test_recommend_video_matches_creator_theme():
    rec = recommend_video([{"feature": "media", "item": "Minecraft speedrun", "detail": {"artist": "PewDiePie", "app": "YouTube"}}])
    assert rec is not None
    assert rec["creator"] == "DanTDM"
    assert rec["theme"] == "gaming"
    assert "PewDiePie" in rec["matched_on"]
    assert rec["redeemed"] is True


def test_recommend_video_matches_topic():
    rec = recommend_video([{"feature": "media", "item": "Science experiment for kids", "detail": {"app": "YouTube"}}])
    assert rec is not None
    assert rec["creator"] == "Mark Rober"
    assert rec["theme"] == "science"


def test_recommend_video_redirects_when_no_redeemed():
    rec = recommend_video([{"feature": "media", "item": "Daily news roundup", "detail": {"app": "YouTube"}}])
    assert rec is not None
    assert rec["redeemed"] is False
    assert rec["creator"] is None
    assert "Proverbs" in (rec.get("redirect_to") or "")


def test_recommend_video_returns_none_for_unknown():
    rec = recommend_video([{"feature": "media", "item": "obscure clip", "detail": {"artist": "nobody", "app": "x"}}])
    assert rec is None


def test_recommend_video_empty_input():
    assert recommend_video([]) is None
    assert recommend_video(None) is None


def test_all_themes_non_empty():
    themes = all_themes()
    assert len(themes) >= 8
    for theme in themes:
        assert theme["reason"]
