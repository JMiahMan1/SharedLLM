# services/bible/devotionals/blb.py
"""blb.org devotionals, as deep links.

Blue Letter Bible publishes a devotional library that is the study base this
feature was built on. We link into it rather than vendoring it: there is no
public API (see docs/BIBLE_STUDY.md "Blue Letter Bible"), scraping their
devotionals would be both brittle and a licensing problem, and a deep link
sends the reader to the actual devotional with its Scripture Index and Calendar
Index intact.

Everything here depends on the ``blb_base_url`` global setting. Unset is a loud
configuration error naming the setting -- never a guess at blb.org's host.
"""
from __future__ import annotations

from services.bible.devotionals.base import DevotionalEntry, DevotionalSource

# (work slug, human title, url template). "{doy}" and "{part}" are substituted.
BLB_WORKS: tuple[tuple[str, str, str], ...] = (
    ("dbdbg", "Day by Day by Grace", "/devotionals/dbdbg/view.cfm?doy={doy}"),
    ("me-am", "Morning and Evening (Morning)", "/devotionals/me/view.cfm?Time=am"),
    ("me-pm", "Morning and Evening (Evening)", "/devotionals/me/view.cfm?Time=pm"),
    ("promises", "BLB Daily Promises", "/devotionals/promises/view.cfm?doy={doy}"),
    ("checkbook", "Faith's Checkbook (Spurgeon)", "/devotionals/checkbook/view.cfm?doy={doy}"),
)


class BlbDevotionalSource(DevotionalSource):
    code = "blb"
    title = "Blue Letter Bible Devotionals"
    priority = 10
    works = tuple(slug for slug, _title, _url in BLB_WORKS)

    def __init__(self, base_url: str = ""):
        self.base_url = (base_url or "").rstrip("/")

    def configured(self) -> bool:
        return bool(self.base_url)

    def unconfigured_reason(self) -> str:
        return (
            "Global setting 'blb_base_url' is not set, so blb.org devotional deep "
            "links cannot be built. Set it in Settings > Bible."
        )

    def url_for(self, work: str, day_of_year: int) -> str:
        template = next((url for slug, _t, url in BLB_WORKS if slug == work), None)
        if template is None:
            raise ValueError(f"unknown BLB devotional work: {work!r}")
        return f"{self.base_url}{template.format(doy=day_of_year)}"

    def entry_for(self, work: str, day_of_year: int) -> DevotionalEntry | None:
        if not self.configured():
            return None
        if work not in self.works:
            return None
        title = next(label for slug, label, _url in BLB_WORKS if slug == work)
        return DevotionalEntry(
            source=self.code,
            work=work,
            title=f"{title} — Day {day_of_year}",
            day_of_year=day_of_year,
            kind="link",
            url=self.url_for(work, day_of_year),
        )