from services.common.game_recommendations import all_themes, match_game, recommend_game


def _day(*apps):
    return {"day": "2026-10-23", "apps": [{"package": p, "millis": m * 60000} for p, m in apps]}


def test_the_most_played_theme_decides():
    rec = recommend_game([_day(("com.epicgames.fortnite", 90), ("com.roblox.client", 30))])
    assert rec["theme"] == "shooter" and rec["game"] == "Rocket League"
    assert rec["matched_on"] == "Fortnite" and rec["redeemed"] is True


def test_time_adds_up_across_the_week():
    days = [_day(("com.roblox.client", 40)), _day(("com.roblox.client", 40)), _day(("com.epicgames.fortnite", 60))]
    assert recommend_game(days)["theme"] == "open sandbox"


def test_package_names_read_as_their_game():
    assert match_game("com.activision.callofduty.shooter") == ("shooter", "Call of Duty")
    assert match_game("com.dts.freefireth") == ("shooter", "Free Fire")
    assert match_game("com.rockstargames.gtasa") == ("mature open world", "Grand Theft Auto")
    assert match_game("com.moonactive.coinmaster") == ("gambling", "Coin Master")


def test_good_fits_and_unknown_apps_get_nothing():
    assert recommend_game([_day(("com.mojang.minecraftpe", 300), ("com.nintendo.zaaa", 60), ("com.spotify.music", 90))]) is None
    assert match_game("com.allegory.reader") is None  # "lego" only as a word


def test_gambling_games_are_redirected():
    rec = recommend_game([_day(("com.moonactive.coinmaster", 25))])
    assert rec["redeemed"] is False and rec["game"] is None and "board game" in rec["redirect_to"]


def test_a_stray_minute_decides_nothing_but_media_titles_count():
    assert recommend_game([_day(("com.epicgames.fortnite", 0.2))]) is None
    rec = recommend_game([], [{"feature": "media", "item": "Poppy Playtime Chapter 3", "detail": {"app": "youtube"}}])
    assert rec["theme"] == "horror" and rec["matched_on"] == "Poppy Playtime"


def test_every_theme_is_complete():
    for theme in all_themes():
        assert theme["reason"]
        assert theme["game"] if theme["redeemed"] else theme["redirect_to"]
