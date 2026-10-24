"""Prayer and guidance: prompts follow what actually happened, most important first."""

from services.common.guidance import PROMPTS, guidance, signals


def test_concerns_come_before_habits_and_faith_is_noticed():
    recap = {
        "concerns": [{"category": "bullying"}, {"category": "self_harm"}],
        "screen": {"late_nights": 3, "change_pct": 40},
        "web": {"blocked": [{"domain": "x.com", "count": 2}]},
        "faith": {"bible_days": 2},
    }
    assert signals(recap) == ["self_harm", "bullying", "late_nights", "screen_up", "blocked", "bible"]
    prompts = guidance(recap, "Emma")
    assert [p["signal"] for p in prompts] == ["self_harm", "bullying", "late_nights", "screen_up"]
    assert "Emma" in prompts[0]["prayer"] and prompts[0]["ref"] == "Psalm 34:18"


def test_a_quiet_week_gets_a_blessing_not_a_worry():
    [p] = guidance({"screen": {"late_nights": 1, "change_pct": 5}}, "Sam")
    assert p["signal"] == "quiet" and "Sam" in p["prayer"]


def test_every_prompt_is_complete():
    for key, p in PROMPTS.items():
        assert all(p.get(field) for field in ("title", "ref", "verse", "prayer", "starter")), key
        assert "{name}" in p["prayer"], key
