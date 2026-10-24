"""Prayer and guidance for parents (Phase 11, step 7).

Pairs a child's real week (the recap) with Scripture: a prayer prompt, a
verse and a conversation starter for each thing that actually happened, most
important first. A reviewed map, not a model: every prompt can be read here,
and a week with nothing notable gets a general blessing rather than an
invented worry. Verses are the King James Version (public domain).

Nothing here is ever sent to the child. A parent reads it, and may start a
gentle note from a starter, which they edit and send themselves.
"""

from __future__ import annotations

from typing import Any

PROMPTS: dict[str, dict[str, str]] = {
    "self_harm": {
        "title": "Hurting inside",
        "ref": "Psalm 34:18",
        "verse": "The LORD is nigh unto them that are of a broken heart; and saveth such as be of a contrite spirit.",
        "prayer": "Pray for {name} to know they are not alone, and for you to be a safe place to say anything.",
        "starter": "I love you and I want to hear how you are really doing. Nothing you tell me will change that.",
    },
    "exploitation": {
        "title": "Someone asking for secrets",
        "ref": "Psalm 121:7",
        "verse": "The LORD shall preserve thee from all evil: he shall preserve thy soul.",
        "prayer": "Pray for protection over {name}, and for wisdom to talk about it without shame.",
        "starter": "If anyone ever asks you to keep a secret from us or to send pictures, you can always tell me. You will not be in trouble.",
    },
    "violence": {
        "title": "Anger or threats",
        "ref": "James 1:19-20",
        "verse": "Let every man be swift to hear, slow to speak, slow to wrath: for the wrath of man worketh not the righteousness of God.",
        "prayer": "Pray for peace in {name}'s heart and for anything frightening to come into the light.",
        "starter": "I noticed something that worried me. Can we talk about what's been going on?",
    },
    "bullying": {
        "title": "Unkind words",
        "ref": "Psalm 139:14",
        "verse": "I will praise thee; for I am fearfully and wonderfully made: marvellous are thy works.",
        "prayer": "Pray for {name} to hear God's voice about who they are louder than anyone else's.",
        "starter": "Has anyone said anything to you lately that hurt? I want to know.",
    },
    "sexual": {
        "title": "What they are seeing",
        "ref": "Philippians 4:8",
        "verse": "Whatsoever things are pure, whatsoever things are lovely ... think on these things.",
        "prayer": "Pray for a pure heart for {name} and for honest, unembarrassed talk at home.",
        "starter": "There's a lot online that isn't good for us. Have you come across anything that made you uncomfortable?",
    },
    "drugs": {
        "title": "Drugs",
        "ref": "1 Corinthians 6:19",
        "verse": "What? know ye not that your body is the temple of the Holy Ghost which is in you?",
        "prayer": "Pray for good friends around {name} and courage to say no.",
        "starter": "Are any of your friends getting into stuff like that? What do you think about it?",
    },
    "secrecy": {
        "title": "Keeping things hidden",
        "ref": "Proverbs 28:13",
        "verse": "He that covereth his sins shall not prosper: but whoso confesseth and forsaketh them shall have mercy.",
        "prayer": "Pray for openness between you and {name}, and that hidden things come out gently.",
        "starter": "You can tell me anything, even something you think I won't like.",
    },
    "substances": {
        "title": "Alcohol or vaping",
        "ref": "Proverbs 20:1",
        "verse": "Wine is a mocker, strong drink is raging: and whosoever is deceived thereby is not wise.",
        "prayer": "Pray for wise choices and friends who build {name} up.",
        "starter": "What do kids at school think about vaping? Do people pressure you?",
    },
    "gambling": {
        "title": "Betting and chance",
        "ref": "Hebrews 13:5",
        "verse": "Let your conversation be without covetousness; and be content with such things as ye have.",
        "prayer": "Pray for contentment for {name} and a healthy view of money.",
        "starter": "I saw some betting stuff come up. What's that about?",
    },
    "late_nights": {
        "title": "Late nights",
        "ref": "Psalm 4:8",
        "verse": "I will both lay me down in peace, and sleep: for thou, LORD, only makest me dwell in safety.",
        "prayer": "Pray for rest for {name}, and for whatever keeps them up at night.",
        "starter": "You've been up late a few nights. Is something on your mind, or is it just hard to put the phone down?",
    },
    "screen_up": {
        "title": "More time on the phone",
        "ref": "Ephesians 5:15-16",
        "verse": "See then that ye walk circumspectly, not as fools, but as wise, redeeming the time.",
        "prayer": "Pray for {name} to find joy away from the screen, and for a plan you make together.",
        "starter": "What's been the best thing on your phone this week? Want to do something together this weekend?",
    },
    "blocked": {
        "title": "Sites that were blocked",
        "ref": "Proverbs 4:23",
        "verse": "Keep thy heart with all diligence; for out of it are the issues of life.",
        "prayer": "Pray for {name}'s curiosity to lead somewhere good.",
        "starter": "Some sites got blocked on your phone this week. Was there something you were looking for?",
    },
    "bible": {
        "title": "Reading the Word",
        "ref": "Psalm 119:105",
        "verse": "Thy word is a lamp unto my feet, and a light unto my path.",
        "prayer": "Thank God for {name}'s time in Scripture and ask Him to make it take root.",
        "starter": "I saw you've been reading the Bible. What's stood out to you?",
    },
    "quiet": {
        "title": "A quiet week",
        "ref": "Numbers 6:24-26",
        "verse": "The LORD bless thee, and keep thee: the LORD make his face shine upon thee, and be gracious unto thee.",
        "prayer": "Bless {name} by name, and thank God for an ordinary week.",
        "starter": "What was the best part of your week?",
    },
}

ORDER = ("self_harm", "exploitation", "violence", "bullying", "sexual", "drugs", "secrecy",
         "substances", "gambling", "late_nights", "screen_up", "blocked", "bible")


def signals(recap: dict[str, Any]) -> list[str]:
    """What happened this week that a prompt speaks to, most important first."""
    found = {c.get("category") for c in recap.get("concerns") or []}
    screen = recap.get("screen") or {}
    if (screen.get("late_nights") or 0) >= 2:
        found.add("late_nights")
    if (screen.get("change_pct") or 0) >= 25:
        found.add("screen_up")
    if any((b.get("count") or 0) > 0 for b in (recap.get("web") or {}).get("blocked") or []):
        found.add("blocked")
    if ((recap.get("faith") or {}).get("bible_days") or 0) >= 1:
        found.add("bible")
    return [s for s in ORDER if s in found]


def guidance(recap: dict[str, Any], name: str, limit: int = 4) -> list[dict[str, str]]:
    """Up to ``limit`` prompts for the week; a quiet week gets a blessing."""
    chosen = signals(recap)[:limit] or ["quiet"]
    out = []
    for key in chosen:
        p = PROMPTS[key]
        out.append({
            "signal": key,
            "title": p["title"],
            "ref": p["ref"],
            "verse": p["verse"],
            "prayer": p["prayer"].format(name=name),
            "starter": p["starter"],
        })
    return out
