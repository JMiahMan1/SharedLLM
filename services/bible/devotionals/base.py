# services/bible/devotionals/base.py
"""The contract every devotional source implements.

Devotionals are the one place in the Bible app where the content is somebody
else's, so the source boundary has to be explicit: a source either has today's
entry, or it does not, and the registry moves on. No source is allowed to
invent a reflection or quietly substitute one devotional for another.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class DevotionalEntry:
    """One day's devotional.

    ``kind`` is "text" for vendored text we can render inline and "link" for a
    pointer at another site. The BLB devotionals are all "link": we do not
    scrape or republish them, we deep-link into them.
    """

    source: str
    work: str
    title: str
    day_of_year: int
    kind: str = "text"
    reference: str = ""
    text: str = ""
    url: str = ""
    month: int = 0
    day: int = 0

    def payload(self) -> dict:
        return {
            "source": self.source,
            "work": self.work,
            "title": self.title,
            "day_of_year": self.day_of_year,
            "kind": self.kind,
            "reference": self.reference,
            "text": self.text,
            "url": self.url,
            "month": self.month,
            "day": self.day,
        }


@dataclass
class DevotionalSource:
    """Base class. Subclasses set ``code``/``title`` and implement ``works``
    and ``entry_for``.

    ``configured`` and ``unconfigured_reason`` exist so the registry can tell
    the user *which* source is missing and *how* to fix it, rather than
    dropping a devotional off the home screen with no explanation.
    """

    code: str = ""
    title: str = ""
    priority: int = 100
    works: tuple[str, ...] = ()

    def configured(self) -> bool:
        return True

    def unconfigured_reason(self) -> str:
        return ""

    def entry_for(self, work: str, day_of_year: int) -> DevotionalEntry | None:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "priority": self.priority,
            "works": list(self.works),
            "configured": self.configured(),
            "reason": "" if self.configured() else self.unconfigured_reason(),
        }


def day_of_year(day: date) -> int:
    """Day-of-year addressing, on the 365-day convention devotionals use.

    February 29 resolves to 28 so the count matches the ``?doy=`` URLs on
    every devotional site rather than sliding by one for nine weeks a year.
    """
    if day.month == 2 and day.day == 29:
        return date(2001, 2, 28).timetuple().tm_yday
    return date(2001, day.month, day.day).timetuple().tm_yday