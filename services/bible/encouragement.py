"""Encouraging verses for Bible reading milestones and returns.

These verses are chosen to be warm, non-preachy, and broadly encouraging.
Each is attributed with book, chapter, and verse.
"""
from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass(frozen=True)
class EncouragingVerse:
    reference: str
    text: str
    book: str
    chapter: int
    verse: int


# Verses for when someone returns after a break
WELCOME_BACK_VERSES: list[EncouragingVerse] = [
    EncouragingVerse(
        reference="Lamentations 3:22-23",
        text="The steadfast love of the LORD never ceases; his mercies never come to an end; they are new every morning; great is your faithfulness.",
        book="Lamentations",
        chapter=3,
        verse=22,
    ),
    EncouragingVerse(
        reference="Psalm 103:13-14",
        text="As a father shows compassion to his children, so the LORD shows compassion to those who fear him. For he knows our frame; he remembers that we are dust.",
        book="Psalm",
        chapter=103,
        verse=13,
    ),
    EncouragingVerse(
        reference="Isaiah 40:31",
        text="But they who wait for the LORD shall renew their strength; they shall mount up with wings like eagles; they shall run and not be weary; they shall walk and not faint.",
        book="Isaiah",
        chapter=40,
        verse=31,
    ),
    EncouragingVerse(
        reference="Psalm 119:105",
        text="Your word is a lamp to my feet and a light to my path.",
        book="Psalm",
        chapter=119,
        verse=105,
    ),
    EncouragingVerse(
        reference="2 Corinthians 12:9",
        text="But he said to me, 'My grace is sufficient for you, for my power is made perfect in weakness.'",
        book="2 Corinthians",
        chapter=12,
        verse=9,
    ),
    EncouragingVerse(
        reference="Psalm 37:23-24",
        text="The steps of a man are established by the LORD, when he delights in his way; though he fall, he shall not be cast headlong, for the LORD upholds his hand.",
        book="Psalm",
        chapter=37,
        verse=23,
    ),
    EncouragingVerse(
        reference="Jeremiah 29:11",
        text="For I know the plans I have for you, declares the LORD, plans for welfare and not for evil, to give you a future and a hope.",
        book="Jeremiah",
        chapter=29,
        verse=11,
    ),
    EncouragingVerse(
        reference="Philippians 4:13",
        text="I can do all things through him who strengthens me.",
        book="Philippians",
        chapter=4,
        verse=13,
    ),
    EncouragingVerse(
        reference="Psalm 27:14",
        text="Wait for the LORD; be strong, and let your heart take courage; wait for the LORD!",
        book="Psalm",
        chapter=27,
        verse=14,
    ),
    EncouragingVerse(
        reference="Isaiah 43:18-19",
        text="Remember not the former things, nor consider the things of old. Behold, I am doing a new thing; now it springs forth, do you not perceive it?",
        book="Isaiah",
        chapter=43,
        verse=18,
    ),
]

# Verses for streak milestones
STREAK_MILESTONE_VERSES: dict[int, EncouragingVerse] = {
    1: EncouragingVerse(
        reference="Psalm 1:2-3",
        text="But his delight is in the law of the LORD, and on his law he meditates day and night. He is like a tree planted by streams of water.",
        book="Psalm",
        chapter=1,
        verse=2,
    ),
    3: EncouragingVerse(
        reference="Ecclesiastes 4:12",
        text="And though a man might prevail against one who is alone, two will withstand him—a threefold cord is not quickly broken.",
        book="Ecclesiastes",
        chapter=4,
        verse=12,
    ),
    7: EncouragingVerse(
        reference="Psalm 119:164",
        text="Seven times a day I praise you for your righteous rules.",
        book="Psalm",
        chapter=119,
        verse=164,
    ),
    14: EncouragingVerse(
        reference="Psalm 119:97",
        text="Oh how I love your law! It is my meditation all the day.",
        book="Psalm",
        chapter=119,
        verse=97,
    ),
    30: EncouragingVerse(
        reference="Psalm 119:11",
        text="I have stored up your word in my heart, that I might not sin against you.",
        book="Psalm",
        chapter=119,
        verse=11,
    ),
    100: EncouragingVerse(
        reference="Psalm 119:9-11",
        text="How can a young man keep his way pure? By guarding it according to your word. With my whole heart I seek you; let me not wander from your commandments! I have stored up your word in my heart, that I might not sin against you.",
        book="Psalm",
        chapter=119,
        verse=9,
    ),
}

# General encouraging verses for daily reminders
DAILY_ENCOURAGEMENT_VERSES: list[EncouragingVerse] = [
    EncouragingVerse(
        reference="Psalm 119:18",
        text="Open my eyes, that I may behold wondrous things out of your law.",
        book="Psalm",
        chapter=119,
        verse=18,
    ),
    EncouragingVerse(
        reference="James 1:22",
        text="But be doers of the word, and not hearers only, deceiving yourselves.",
        book="James",
        chapter=1,
        verse=22,
    ),
    EncouragingVerse(
        reference="Colossians 3:16",
        text="Let the word of Christ dwell in you richly, teaching and admonishing one another in all wisdom.",
        book="Colossians",
        chapter=3,
        verse=16,
    ),
    EncouragingVerse(
        reference="Psalm 19:7",
        text="The law of the LORD is perfect, reviving the soul; the testimony of the LORD is sure, making wise the simple.",
        book="Psalm",
        chapter=19,
        verse=7,
    ),
    EncouragingVerse(
        reference="Matthew 4:4",
        text="Man shall not live by bread alone, but by every word that comes from the mouth of God.",
        book="Matthew",
        chapter=4,
        verse=4,
    ),
    EncouragingVerse(
        reference="Joshua 1:8",
        text="This Book of the Law shall not depart from your mouth, but you shall meditate on it day and night.",
        book="Joshua",
        chapter=1,
        verse=8,
    ),
    EncouragingVerse(
        reference="Psalm 119:11",
        text="I have stored up your word in my heart, that I might not sin against you.",
        book="Psalm",
        chapter=119,
        verse=11,
    ),
    EncouragingVerse(
        reference="2 Timothy 3:16-17",
        text="All Scripture is breathed out by God and profitable for teaching, for reproof, for correction, and for training in righteousness.",
        book="2 Timothy",
        chapter=3,
        verse=16,
    ),
]


def pick_welcome_back() -> EncouragingVerse:
    """Pick a random welcome-back verse."""
    return random.choice(WELCOME_BACK_VERSES)


def pick_streak_milestone(days: int) -> EncouragingVerse | None:
    """Get the verse for a specific streak milestone, if defined."""
    return STREAK_MILESTONE_VERSES.get(days)


def pick_daily_encouragement() -> EncouragingVerse:
    """Pick a random daily encouragement verse."""
    return random.choice(DAILY_ENCOURAGEMENT_VERSES)