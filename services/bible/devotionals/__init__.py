"""Devotional sources for the Bible service.

Add a source by subclassing :class:`DevotionalSource` and registering it in
``registry._SOURCES``. Sources are consulted in ``priority`` order and the first
one with the day wins; every source skipped is reported with its reason.
"""

from services.bible.devotionals.base import DevotionalEntry, DevotionalSource, day_of_year

__all__ = ["DevotionalEntry", "DevotionalSource", "day_of_year"]