"""App vetting rules: the age policy, verdicts, who hears about them, unused apps."""
from datetime import date

import pytest

from services.common import app_vetting as av

TODAY = date(2026, 11, 15)
DAY_MS = 86_400_000
NOW_MS = 1_800_000_000_000


@pytest.mark.parametrize(("age", "rating"), [
    (6, "Everyone"), (9, "Everyone"), (10, "Everyone 10+"), (12, "Everyone 10+"),
    (13, "Teen"), (16, "Teen"), (17, "Mature 17+"), (18, "Adults only 18+"), (None, None),
])
def test_the_default_limit_follows_the_age(age, rating):
    assert av.default_max_rating(age) == rating


def test_age_counts_the_birthday_itself():
    assert av.age_on(date(2016, 11, 15), TODAY) == 10
    assert av.age_on(date(2016, 11, 16), TODAY) == 9
    assert av.age_on(None, TODAY) is None


@pytest.mark.parametrize("bad", ["2016/11/15", "15-11-2016", "", None, 2016, "2027-01-01", "1800-01-01"])
def test_a_bad_birth_date_is_refused_with_a_reason(bad):
    with pytest.raises(ValueError):
        av.parse_birth_date(bad, TODAY)


def test_a_parent_override_beats_the_age_default():
    policy = av.effective_policy({"max_rating": "Teen"}, "2016-01-01", TODAY)
    assert (policy["age"], policy["default_max_rating"], policy["effective_max_rating"]) == (10, "Everyone 10+", "Teen")
    plain = av.effective_policy({}, "2016-01-01", TODAY)
    assert plain["effective_max_rating"] == "Everyone 10+" and plain["max_rating"] is None


def test_no_birth_date_means_no_limit_rather_than_a_guess():
    policy = av.effective_policy({}, None, TODAY)
    assert policy["age"] is None and policy["effective_max_rating"] is None


def test_a_malformed_stored_policy_is_cleaned_not_trusted():
    policy = av.clean_policy({"max_rating": "PG-13", "apps": {"com.x.y": "allow", "nope": "flag", "com.a.b": "maybe"},
                              "unused_days": 3, "dismissed": {"com.x.y": 5}})
    assert "max_rating" not in policy
    assert policy["apps"] == {"com.x.y": "allow"}
    assert policy["unused_days"] == av.DEFAULT_UNUSED_DAYS
    assert policy["dismissed"] == {}


def _entry(rating="Teen", concerns=None, status="found", review="ready", **listing):
    body = {"title": "Game", "content_rating": rating, "contains_ads": False, "in_app_purchases": False,
            "interactive_elements": [], **listing}
    entry = {"listing": {"status": status, "hash": "h1", "listing": body if status == "found" else None}}
    if review:
        entry["review"] = {"status": review, "listing_hash": "h1", "summary": "A game.", "concerns": concerns or []}
    return entry


POLICY_10 = av.effective_policy({}, "2016-01-01", TODAY)  # age 10, Everyone 10+


def test_an_app_rated_above_the_limit_is_flagged_high():
    verdict = av.assess({"package": "com.roblox.client", "label": "Roblox"}, _entry("Teen"), POLICY_10)
    assert verdict["over_age"] is True
    assert verdict["level"] == "high"
    assert [f["reason"] for f in verdict["flags"]] == ["over_age"]
    assert "above the Everyone 10+ limit" in verdict["flags"][0]["text"]


def test_an_app_within_the_limit_with_no_concerns_is_clear():
    verdict = av.assess({"package": "com.example.app"}, _entry("Everyone"), POLICY_10)
    assert verdict["over_age"] is False and verdict["flags"] == [] and verdict["level"] is None


def test_each_review_concern_is_a_flag_at_its_severity():
    verdict = av.assess({"package": "com.example.app"}, _entry("Everyone", [
        {"category": "contact_strangers", "severity": "severe", "why": "Strangers message kids", "evidence": []},
        {"category": "ads", "severity": "medium", "why": "An ad every round", "evidence": []},
    ]), POLICY_10)
    assert verdict["level"] == "severe"
    assert {f["category"] for f in verdict["flags"]} == {"contact_strangers", "ads"}


def test_a_review_of_an_older_listing_is_not_used():
    entry = _entry("Everyone", [{"category": "ads", "severity": "medium", "why": "Ads", "evidence": []}])
    entry["review"]["listing_hash"] = "old"
    verdict = av.assess({"package": "com.example.app"}, entry, POLICY_10)
    assert verdict["concerns"] == [] and verdict["review_state"] == "ready"
    assert verdict["summary"] is None


def test_a_parent_allow_silences_and_a_parent_flag_raises():
    allowed = av.effective_policy({"apps": {"com.roblox.client": "allow"}}, "2016-01-01", TODAY)
    verdict = av.assess({"package": "com.roblox.client"}, _entry("Teen"), allowed)
    assert verdict["level"] is None and av.flagged_for(verdict, "all") == [] and av.notifies(verdict, "all") == []
    flagged = av.effective_policy({"apps": {"com.example.app": "flag"}}, "2016-01-01", TODAY)
    verdict = av.assess({"package": "com.example.app"}, _entry("Everyone"), flagged)
    assert [f["reason"] for f in verdict["flags"]] == ["parent_flag"]


def test_an_app_not_on_play_is_judged_on_its_permissions():
    app = {"package": "com.sideloaded.thing", "installer": None, "permissions": ["location", "camera"]}
    verdict = av.assess(app, {"listing": {"status": "not_on_play"}}, POLICY_10)
    reasons = {f["reason"]: f for f in verdict["flags"]}
    assert reasons["permissions"]["level"] == "high" and "location" in reasons["permissions"]["text"]
    assert reasons["sideloaded"]["level"] == "medium"
    from_store = av.assess({**app, "installer": "com.sec.android.app.samsungapps", "permissions": []},
                           {"listing": {"status": "not_on_play"}}, POLICY_10)
    assert from_store["flags"] == [] and from_store["store"] == "Galaxy Store"


def test_nothing_known_yet_reads_as_checking():
    verdict = av.assess({"package": "com.example.app"}, None, POLICY_10)
    assert verdict["lookup"] == "checking" and verdict["over_age"] is None
    assert av.verdict_line(verdict) == "Jarvis is checking its Play listing and reviews."


@pytest.mark.parametrize(("level", "medium", "high", "severe"), [
    ("all", True, True, True), ("high", False, True, True), ("severe", False, False, True), ("off", False, False, False),
])
def test_notices_follow_each_parents_level(level, medium, high, severe):
    def verdict(sev):
        return av.assess({"package": "com.example.app"},
                         _entry("Everyone", [{"category": "ads", "severity": sev, "why": "x", "evidence": []}]), POLICY_10)
    assert bool(av.notifies(verdict("medium"), level)) is medium
    assert bool(av.notifies(verdict("high"), level)) is high
    assert bool(av.notifies(verdict("severe"), level)) is severe


def test_self_harm_notifies_even_a_parent_who_switched_off():
    verdict = av.assess({"package": "com.example.app"},
                        _entry("Everyone", [{"category": "self_harm", "severity": "medium", "why": "x", "evidence": []}]),
                        POLICY_10)
    assert av.notifies(verdict, "off")


def test_the_age_limit_is_always_put_first_but_notifies_per_level():
    verdict = av.assess({"package": "com.roblox.client"}, _entry("Teen"), POLICY_10)
    assert av.flagged_for(verdict, "severe")  # shown at the top of the card
    assert av.notifies(verdict, "severe") == []  # but a "severe only" parent is not paged
    assert av.notifies(verdict, "high")


def test_a_dismissal_holds_until_the_verdict_changes():
    first = av.assess({"package": "com.roblox.client"}, _entry("Teen"), POLICY_10)
    kept = av.effective_policy({"dismissed": {"com.roblox.client": first["fingerprint"]}}, "2016-01-01", TODAY)
    again = av.assess({"package": "com.roblox.client"}, _entry("Teen"), kept)
    assert again["dismissed"] and av.notifies(again, "all") == []
    worse = av.assess({"package": "com.roblox.client"}, _entry("Teen", [
        {"category": "contact_strangers", "severity": "high", "why": "x", "evidence": []}]), kept)
    assert not worse["dismissed"] and av.notifies(worse, "all")


def test_questionable_apps_sort_first_most_serious_first():
    clear = av.assess({"package": "com.a.clear", "label": "Aardvark"}, _entry("Everyone"), POLICY_10)
    over = av.assess({"package": "com.b.over", "label": "Bee"}, _entry("Teen"), POLICY_10)
    severe = av.assess({"package": "com.c.bad", "label": "Cat"}, _entry("Everyone", [
        {"category": "sexual", "severity": "severe", "why": "x", "evidence": []}]), POLICY_10)
    assert [v["label"] for v in av.sort_for_parent([clear, over, severe], "high")] == ["Cat", "Bee", "Aardvark"]


def test_inventories_merge_per_package_across_devices():
    merged = av.merge_inventories([
        {"device": "phone", "usage_access": True, "apps": [
            {"package": "com.x.game", "label": "Game", "installer": None, "first_install": 5, "last_update": 7,
             "last_used": 100, "launchable": True, "permissions": ["camera"], "size_bytes": 10}]},
        {"device": "tablet", "usage_access": False, "apps": [
            {"package": "com.x.game", "label": "", "installer": "com.android.vending", "first_install": 3,
             "last_update": 9, "last_used": 200, "launchable": True, "permissions": ["location"], "size_bytes": 30}]},
    ])
    assert len(merged) == 1
    row = merged[0]
    assert row["devices"] == ["phone", "tablet"] and row["last_used"] == 200 and row["first_install"] == 3
    assert row["permissions"] == ["camera", "location"] and row["size_bytes"] == 30 and row["usage_access"]


def test_an_inventory_row_is_bounded_and_cleaned():
    assert av.clean_inventory_app({"package": "not a package"}) is None
    row = av.clean_inventory_app({"package": "com.x.y", "label": "x" * 500, "permissions": ["camera", "teleport"],
                                  "last_used": -5, "size_bytes": True, "installer": "../evil"})
    assert len(row["label"]) == 120 and row["permissions"] == ["camera"]
    assert row["last_used"] is None and row["size_bytes"] is None and row["installer"] is None


def _app(package, last_used=None, first_install=None, usage_access=True):
    return {"package": package, "label": package, "last_used": last_used, "first_install": first_install,
            "usage_access": usage_access, "launchable": True, "devices": ["phone"], "size_bytes": None}


def test_apps_not_used_for_the_window_are_suggested_longest_first():
    policy = av.effective_policy({"unused_days": 30}, None, TODAY)
    apps = [
        _app("com.recent.app", last_used=NOW_MS - 2 * DAY_MS),
        _app("com.month.app", last_used=NOW_MS - 40 * DAY_MS),
        _app("com.year.app", last_used=NOW_MS - 300 * DAY_MS),
        _app("com.never.app", first_install=NOW_MS - 90 * DAY_MS),
        _app("com.new.app", first_install=NOW_MS - 3 * DAY_MS),
        _app("com.unknown.app", first_install=NOW_MS - 90 * DAY_MS, usage_access=False),
        _app("com.jarvisos.app", last_used=NOW_MS - 300 * DAY_MS),
    ]
    out = av.unused_apps(apps, policy, NOW_MS, "com.jarvisos.app")
    assert [a["package"] for a in out] == ["com.year.app", "com.never.app", "com.month.app"]
    assert out[1]["never_used"] is True and out[0]["days_unused"] == 300


def test_a_kept_app_stays_hidden_until_it_is_used_again():
    from datetime import UTC, datetime

    kept_at = datetime.fromtimestamp((NOW_MS - 10 * DAY_MS) / 1000, UTC).isoformat()
    policy = av.effective_policy({"kept": {"com.year.app": kept_at}}, None, TODAY)
    assert av.unused_apps([_app("com.year.app", last_used=NOW_MS - 300 * DAY_MS)], policy, NOW_MS) == []
    # Used after the parent kept it, then unused again for the window: suggested again.
    used_later = _app("com.year.app", last_used=NOW_MS - 5 * DAY_MS)
    long_policy = av.effective_policy({"kept": {"com.year.app": kept_at}, "unused_days": 7}, None, TODAY)
    assert av.unused_apps([used_later], long_policy, NOW_MS + 30 * DAY_MS)


def test_the_verdict_line_names_the_rating_concerns_and_facts():
    verdict = av.assess({"package": "com.roblox.client", "label": "Roblox"}, _entry(
        "Teen", [{"category": "contact_strangers", "severity": "high", "why": "x", "evidence": []}],
        in_app_purchases=True, interactive_elements=["Users Interact", "In-Game Purchases"]), POLICY_10)
    line = av.verdict_line(verdict)
    assert "Rated Teen, above the Everyone 10+ limit" in line
    assert "Watch for: Contact with strangers or chat" in line
    assert "Has in-app purchases; Users Interact" in line
    assert chr(0x2014) not in line and chr(0x2013) not in line


# --- Common Sense Media next to the Play rating ------------------------------


def _cs(age):
    return {"status": "found", "review": {"age": age, "age_label": f"age {age}+", "name": "Example",
                                          "parents_need_to_know": "Parents need to know that it is fine."}}


def test_common_sense_decides_when_it_is_stricter_and_says_so():
    policy = av.effective_policy({}, "2016-01-01", TODAY)  # 10, Everyone 10+
    verdict = av.assess({"package": "com.example.game"}, {**_found("Everyone 10+"), "common_sense": _cs(13)}, policy)
    assert verdict["age_source"] == "common_sense" and verdict["over_age"] is True
    assert verdict["play_over_age"] is False and verdict["common_sense_limit"] == 10
    flag = next(f for f in verdict["flags"] if f["reason"] == "over_age")
    assert flag["source"] == "common_sense" and flag["text"] == "Common Sense Media says age 13+, above the limit of 10"
    assert verdict["common_sense"]["age"] == 13 and verdict["common_sense"]["status"] == "found"
    assert "Common Sense Media says age 13+" in av.verdict_line(verdict)


def test_play_decides_when_it_is_stricter():
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    verdict = av.assess({"package": "com.example.game"}, {**_found("Teen"), "common_sense": _cs(9)}, policy)
    assert verdict["age_source"] == "play" and verdict["over_age"] is True
    assert verdict["common_sense_over_age"] is False
    assert next(f for f in verdict["flags"] if f["reason"] == "over_age")["text"] == "Rated Teen, above the Everyone 10+ limit"
    line = av.verdict_line(verdict)
    assert "Rated Teen, above the Everyone 10+ limit" in line and "Common Sense Media says age 9+" in line


def test_a_tie_is_play_and_a_parents_limit_covers_its_whole_band():
    # A parent allowed Teen for a ten-year-old: Common Sense ages up to 16 are within it.
    policy = av.effective_policy({"max_rating": "Teen"}, "2016-01-01", TODAY)
    within = av.age_assessment("Teen", {"age": 15}, policy)
    assert within["source"] == "common_sense" and within["over_age"] is False and within["common_sense_limit"] == 16
    tie = av.age_assessment("Teen", {"age": 13}, policy)
    assert tie["source"] == "play"


def test_common_sense_alone_still_judges_an_app_without_a_play_rating():
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    verdict = av.assess({"package": "com.example.web"}, {"listing": {"status": "not_on_play"}, "common_sense": _cs(12)},
                        policy)
    assert verdict["age_source"] == "common_sense" and verdict["over_age"] is True


def test_without_a_birth_date_common_sense_cannot_be_compared():
    policy = av.effective_policy({}, None, TODAY)
    out = av.age_assessment("Everyone", {"age": 13}, policy)
    assert out["source"] == "common_sense" and out["over_age"] is None


@pytest.mark.parametrize("status", ["off", "not_found", "error", "checking"])
def test_a_common_sense_lookup_without_a_review_changes_nothing(status):
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    verdict = av.assess({"package": "com.example.game"},
                        {**_found("Everyone"), "common_sense": {"status": status, "reason": "why"}}, policy)
    assert verdict["age_source"] == "play" and verdict["over_age"] is False
    assert verdict["common_sense"] == {"status": status, "reason": "why"}


# --- the security scan in the verdict ---------------------------------------


def test_a_sideloaded_app_with_risky_access_notifies_at_severe():
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    app = av.clean_inventory_app({"package": "com.example.helper", "installer": None, "permissions": ["location"],
                                  "capabilities": ["accessibility"], "special_access": ["accessibility"]})
    verdict = av.assess(app, {"listing": {"status": "not_on_play"}}, policy)
    reasons = [f["reason"] for f in verdict["flags"]]
    assert "sideloaded_risky" in reasons and "sideloaded" not in reasons  # one flag for where it came from
    assert verdict["level"] == "severe" and verdict["security_level"] == "severe"
    assert [f["reason"] for f in av.notifies(verdict, "severe")] == ["sideloaded_risky"]
    assert {f["check"] for f in verdict["security"]} == {"sideloaded", "risky_access"}


def test_a_malware_hit_from_the_check_flags_the_app():
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    app = {**av.clean_inventory_app({"package": "com.example.game", "installer": "com.android.vending"}),
           "malware_check": {"state": "malware", "signature": "ExampleFamily", "reason": None}}
    verdict = av.assess(app, _found("Everyone"), policy)
    assert verdict["level"] == "severe" and verdict["flags"][0]["reason"] == "malware"
    assert verdict["malware_check"]["state"] == "malware"
    assert "known malware sample" in av.verdict_line(verdict)


def test_review_excerpts_and_reviews_read_reach_the_verdict():
    policy = av.effective_policy({}, "2016-01-01", TODAY)
    catalog = _found("Everyone")
    catalog["listing"]["listing"]["reviews_read"] = {"relevant": 40, "newest": 40, "lowest": 12, "state": "done",
                                                     "errors": {}}
    catalog["review"] = {"status": "ready", "listing_hash": "h1", "summary": "Fine.", "concerns": [],
                         "excerpts": [{"category": "ads", "stars": 2, "at": "2026-11-01", "text": "Ads.",
                                       "source": "lowest"}]}
    verdict = av.assess({"package": "com.example.game"}, catalog, policy)
    assert verdict["review_excerpts"][0]["text"] == "Ads." and verdict["reviews_read"]["lowest"] == 12


def _found(rating):
    return _entry(rating)
