"""The fasts the early church kept, and what it taught about fasting.

Each kind is a pattern a person can start from the Fasting card: a window of
hours, a fast until a time of day (the station fasts ended at the ninth hour,
3 PM), or a season of days with a daily rule (Lent, the Nativity fast). Every
kind names its sources. The Scripture and the Fathers below are cited, never
quoted at length; the app reads the verse text from the family's Bible.

The guidance is pastoral, not medical: the early church excused the sick, and
the card says so. Fasting stays adults only (``preferences.SWITCHES``).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

#: Patterns: "hours" (a window), "until" (a time of day, HH:MM), "days" (a season).
KINDS: list[dict[str, Any]] = [
    {
        "key": "station",
        "name": "Wednesday & Friday fast",
        "era": "1st century onward",
        "summary": "The weekly fast of the first Christians: Wednesday for the betrayal of the Lord, Friday for his Passion.",
        "rule": "No food until the ninth hour (3 PM), then a simple meal. Water as needed.",
        "pattern": {"type": "until", "clock": "15:00"},
        "sources": [
            {"cite": "Didache 8.1", "note": "Fast on the fourth day (Wednesday) and the Preparation (Friday), not with the hypocrites on Monday and Thursday."},
            {"cite": "Peter of Alexandria, Canon 15", "note": "Wednesday because the Lord was betrayed, Friday because he suffered for us."},
            {"cite": "Tertullian, On Fasting 2, 10", "note": "The \"station\" days were kept until the ninth hour (written in his later, Montanist years)."},
        ],
        "scripture": ["Matthew 9:15", "Mark 14:10-11", "John 19:30"],
    },
    {
        "key": "evening",
        "name": "Fast until evening",
        "era": "Old Testament and early church",
        "summary": "A day's fast ending with one meal at evening, the common shape of a fast day in Scripture and in the Lenten practice of the early church.",
        "rule": "No food until evening, then one simple meal.",
        "pattern": {"type": "until", "clock": "18:00"},
        "sources": [
            {"cite": "Judges 20:26; 2 Samuel 1:12", "note": "Israel fasted \"until evening\"."},
            {"cite": "Shepherd of Hermas, Similitude 5.3", "note": "On a fast day take only bread and water, reckon what you would have eaten, and give it to the widow, the orphan or the poor."},
        ],
        "scripture": ["Judges 20:26", "2 Samuel 1:12", "Isaiah 58:6-7"],
    },
    {
        "key": "xerophagy",
        "name": "Xerophagy (dry eating)",
        "era": "2nd-4th century",
        "summary": "A strict fast of plain, dry food: bread, salt, vegetables and water, without meat, wine or oil. Kept especially in the days before Pascha.",
        "rule": "Bread, salt, vegetables, fruit and water only, for the day.",
        "pattern": {"type": "until", "clock": "18:00"},
        "sources": [
            {"cite": "Tertullian, On Fasting 1, 9", "note": "Names xerophagies, dry food without meat or wine (written in his Montanist years)."},
            {"cite": "Apostolic Constitutions 5.18", "note": "In the days of the Pascha, bread, salt, vegetables and water."},
        ],
        "scripture": ["Daniel 1:12", "Matthew 4:4"],
    },
    {
        "key": "paschal",
        "name": "Paschal fast (forty hours)",
        "era": "2nd century",
        "summary": "The fast before Easter for the hours the Lord lay in the tomb. Some churches kept one day, some two, some forty hours.",
        "rule": "From Good Friday to the Easter vigil: no food, water as needed. Break the fast with the Resurrection.",
        "pattern": {"type": "hours", "hours": 40},
        "sources": [
            {"cite": "Irenaeus, Letter to Victor (Eusebius, Church History 5.24)", "note": "Christians kept the fast before Pascha for one day, two, more, or forty hours, and kept peace with one another over the differences."},
        ],
        "scripture": ["Matthew 9:15", "Matthew 27:57-66", "Luke 24:1-7"],
        "caution": "A fast over a day: drink water, and stop if you feel faint or unwell.",
    },
    {
        "key": "lent",
        "name": "Great Lent (the Forty Days)",
        "era": "4th century (and earlier)",
        "summary": "The forty days before Pascha, after the Lord's forty days in the wilderness: a season of fasting, prayer and almsgiving.",
        "rule": "Each day: one simple meal toward evening, without meat. Sundays are not fast days.",
        "pattern": {"type": "days", "days": 40, "daily": "One meal toward evening, no meat"},
        "sources": [
            {"cite": "Council of Nicaea, Canon 5", "note": "Has the bishops meet \"before Lent\", so the forty days were already kept in 325."},
            {"cite": "Athanasius, Festal Letters", "note": "Each year announced the forty days and the Paschal fast to the churches."},
            {"cite": "Leo the Great, Sermons on Lent", "note": "Joins the fast to almsgiving, forgiveness and reconciliation."},
        ],
        "scripture": ["Matthew 4:1-4", "Joel 2:12-13", "Isaiah 58:6-9"],
    },
    {
        "key": "nativity",
        "name": "Nativity fast (Advent)",
        "era": "5th century",
        "summary": "A fast of preparation for Christ's birth, from St. Martin's day (November 11) or November 15 until Christmas.",
        "rule": "Abstain from meat; fast until 3 PM on Wednesdays and Fridays.",
        "pattern": {"type": "days", "days": 40, "daily": "No meat; Wednesday and Friday until 3 PM"},
        "sources": [
            {"cite": "Gregory of Tours, History of the Franks 10.31", "note": "Bishop Perpetuus of Tours (c. 480) set fasts from St. Martin's day to Christmas."},
        ],
        "scripture": ["Isaiah 9:2-7", "Luke 1:46-55", "Galatians 4:4-5"],
    },
    {
        "key": "ember",
        "name": "Ember days",
        "era": "5th century (Rome)",
        "summary": "Wednesday, Friday and Saturday in each of the four seasons, given to thanksgiving for the harvest, prayer and almsgiving.",
        "rule": "Three days: fast until 3 PM Wednesday and Friday, with a Saturday vigil.",
        "pattern": {"type": "days", "days": 3, "daily": "Until 3 PM; Saturday vigil"},
        "sources": [
            {"cite": "Leo the Great, Sermon 12 (On the Fast of the Tenth Month)", "note": "Fast on Wednesday and Friday, keep vigil on Saturday, and give alms."},
        ],
        "scripture": ["Psalm 65:9-13", "Zechariah 8:19", "Psalm 104:27-28"],
    },
    {
        "key": "baptism",
        "name": "Fast before baptism",
        "era": "1st-2nd century",
        "summary": "The one baptized, the one baptizing, and any who can, fast one or two days before the baptism and pray with them.",
        "rule": "One or two days of fasting and prayer before the baptism.",
        "pattern": {"type": "hours", "hours": 24},
        "sources": [
            {"cite": "Didache 7.4", "note": "Before baptism let the baptizer and the one baptized fast, and others who are able; one or two days before."},
            {"cite": "Justin Martyr, First Apology 61", "note": "Those to be baptized pray and fast, and the church prays and fasts with them."},
        ],
        "scripture": ["Acts 9:9", "Acts 2:38-39", "Romans 6:3-4"],
    },
    {
        "key": "eucharist",
        "name": "Fast before Communion",
        "era": "3rd-4th century",
        "summary": "Receiving the Lord's Supper before any other food, so that it is the first thing taken that day.",
        "rule": "No food from waking until you have received Communion.",
        "pattern": {"type": "until", "clock": "12:00"},
        "sources": [
            {"cite": "Augustine, Letter 54 (to Januarius)", "note": "Throughout the world the Eucharist is received by those fasting."},
        ],
        "scripture": ["1 Corinthians 11:23-29", "John 6:35"],
    },
    {
        "key": "intercession",
        "name": "Fast of intercession",
        "era": "Apostolic",
        "summary": "A fast for a particular need: for someone, for guidance, or for those who wrong you, joined to prayer.",
        "rule": "Choose the length; pray for the need at each meal you would have eaten.",
        "pattern": {"type": "hours", "hours": 24},
        "sources": [
            {"cite": "Didache 1.3", "note": "Pray for your enemies, and fast for those who persecute you."},
            {"cite": "Polycarp, To the Philippians 7.2", "note": "Watch unto prayer and persevere in fasting."},
        ],
        "scripture": ["Acts 13:2-3", "Acts 14:23", "Ezra 8:21-23", "Esther 4:16", "Daniel 9:3"],
    },
    {
        "key": "daniel",
        "name": "Daniel fast",
        "era": "Scripture",
        "summary": "Three weeks without rich food, meat or wine, as Daniel mourned and sought the Lord.",
        "rule": "Vegetables, grains, fruit and water; no meat, sweets or wine.",
        "pattern": {"type": "days", "days": 21, "daily": "No meat, sweets or wine"},
        "sources": [
            {"cite": "Daniel 10:2-3", "note": "\"I ate no pleasant bread, no meat or wine came into my mouth\" for three full weeks."},
        ],
        "scripture": ["Daniel 10:2-3", "Daniel 1:12", "Psalm 27:4"],
    },
    {
        "key": "custom",
        "name": "My own fast",
        "era": "",
        "summary": "A window you choose.",
        "rule": "",
        "pattern": {"type": "hours", "hours": 16},
        "sources": [],
        "scripture": ["Matthew 6:16-18"],
    },
]

KIND_KEYS = {kind["key"] for kind in KINDS}

#: Why the Church fasts.
WHY_FAST = [
    "Matthew 6:16-18",  # "When you fast": expected of disciples
    "Matthew 9:15",  # the Bridegroom taken away, then they will fast
    "Matthew 4:1-4",  # not by bread alone
    "Joel 2:12-13",
    "Acts 13:2-3",
    "Acts 14:23",
    "Ezra 8:21-23",
    "Nehemiah 1:4",
    "Luke 2:37",  # Anna served God with fastings and prayers
    "1 Corinthians 9:25-27",
]

#: The heart the fast is for.
HEART = [
    "Isaiah 58:3-9",  # the fast that I choose
    "Joel 2:13",  # rend your heart, not your garments
    "Zechariah 7:5-6",  # did you fast for me?
    "Luke 18:9-14",  # "I fast twice a week"
    "Matthew 6:16-18",  # in secret
    "Psalm 51:17",
    "Micah 6:8",
]

#: Psalms the Church has prayed in fasting: the seven penitential psalms, and
#: the psalms that speak of fasting or of hunger for God.
PSALMS = ["Psalm 51", "Psalm 6", "Psalm 32", "Psalm 38", "Psalm 102", "Psalm 130", "Psalm 143",
          "Psalm 35:13", "Psalm 69:10", "Psalm 109:24", "Psalm 42:1-2", "Psalm 63:1-8"]

#: What the Fathers taught about the heart of a fast (paraphrased; cited).
FATHERS = [
    {"cite": "John Chrysostom, Homilies on the Statues 3", "note": "The value of a fast is not in going without food but in turning from sin: let the eye, the ear, the hands and the tongue fast too."},
    {"cite": "Basil the Great, First Homily on Fasting", "note": "Fasting is as old as mankind (the command in Paradise was a fast). Do not wear a gloomy face; the true fast is to be estranged from evil, to bridle the tongue and anger."},
    {"cite": "Shepherd of Hermas, Similitude 5.3", "note": "Keep the commandments; reckon what the day's food would have cost and give it to the widow, the orphan or the poor."},
    {"cite": "Leo the Great, Sermon 12", "note": "Prayer, fasting and almsgiving go together; a fast is completed by mercy to others."},
    {"cite": "Didache 1.3; 8.1", "note": "Fast for those who persecute you, and not as the hypocrites do."},
    {"cite": "Polycarp, To the Philippians 7.2", "note": "Persevere in fasting, watching unto prayer, asking God not to lead us into temptation."},
]

#: The hours of prayer the early church kept (Acts 2:15; 10:9; 3:1), with the
#: Lord's Prayer three times a day (Didache 8.3).
PRAYER_HOURS = [
    {"clock": "09:00", "name": "Third hour", "reason": "The Spirit was poured out at the third hour (Acts 2:15).", "scripture": "Acts 2:15"},
    {"clock": "12:00", "name": "Sixth hour", "reason": "Peter went up to pray at the sixth hour (Acts 10:9); the hour of the Cross.", "scripture": "Acts 10:9"},
    {"clock": "15:00", "name": "Ninth hour", "reason": "The hour of prayer (Acts 3:1), when the Lord gave up his spirit.", "scripture": "Acts 3:1"},
]

#: Practical helps, written for a healthy adult.
HELPS = [
    "Drink water through the day; for a fast over a day, a pinch of salt in water helps.",
    "When a meal time comes, use it: pray the Lord's Prayer (Didache 8.3) or read the day's psalm.",
    "Set aside what you would have spent on food and give it away (Shepherd of Hermas, Similitude 5.3).",
    "Keep the fast quiet: wash your face and go about your day (Matthew 6:17-18).",
    "Break the fast gently with something small and simple.",
    "Rest more, and skip hard exercise on a long fast.",
    "The early church excused the sick. If you are pregnant or nursing, diabetic, on medication, or feel faint, dizzy or unwell, eat and speak to your doctor.",
]

#: Sent to those a fast is shared with, one chosen per fast.
ENCOURAGEMENT = ["Isaiah 58:8-9", "Matthew 6:18", "Psalm 34:8", "Hebrews 4:16", "Philippians 4:6-7",
                 "James 5:16", "Isaiah 40:31", "Psalm 145:18"]


def kind(key: str | None) -> dict | None:
    return next((k for k in KINDS if k["key"] == key), None) if key else None


def name_of(key: str | None) -> str:
    found = kind(key)
    return found["name"] if found and found["key"] != "custom" else "fast"


def pick(items: list[str], seed: int) -> str:
    """One item, the same for the same seed (a fast's id, a day number)."""
    return items[seed % len(items)]


def verse_of_day(day: date) -> str:
    """The fasting verse for a calendar day, the same all day for everyone."""
    pool = WHY_FAST + HEART + PSALMS
    return pool[day.toordinal() % len(pool)]


def planned_end(key: str | None, started: datetime, *, target_hours: int | None, days: int | None,
                until: str | None, tz) -> datetime | None:
    """When a fast of this kind, started at ``started``, is planned to end.

    ``until`` (HH:MM, household time) wins, then ``days``, then ``target_hours``,
    then the kind's own pattern. A time already passed today means tomorrow.
    """
    pattern = (kind(key) or {}).get("pattern") or {}
    clock = until or (pattern.get("clock") if not (days or target_hours) else None)
    if clock:
        hh, mm = (int(part) for part in clock.split(":"))
        local = started.astimezone(tz)
        end = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if end <= local:
            end += timedelta(days=1)
        return end
    span_days = days or (pattern.get("days") if not target_hours else None)
    if span_days:
        return started + timedelta(days=span_days)
    hours = target_hours or pattern.get("hours")
    return started + timedelta(hours=hours) if hours else None


def catalog() -> dict:
    """Everything the Fasting card shows, in one read."""
    return {
        "kinds": KINDS,
        "why": WHY_FAST,
        "heart": HEART,
        "psalms": PSALMS,
        "fathers": FATHERS,
        "prayer_hours": PRAYER_HOURS,
        "helps": HELPS,
    }
