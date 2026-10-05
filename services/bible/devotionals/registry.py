# services/bible/devotionals/registry.py
"""Ordered registry of devotional sources.

Priority order is the product decision: blb.org devotionals first (the study
base this whole feature was built on), then local files an operator drops in,
then anything a later phase registers. The registry never merges or falls back
*within* a source -- it returns the first source that actually has the day, and
reports every source it skipped along with the reason, so the UI can say
"today's devotional is from Blue Letter Bible; your local devotionals folder is
empty" instead of just showing nothing.
"""
from __future__ import annotations

import logging
from datetime import date

from services.bible.devotionals.base import DevotionalEntry, DevotionalSource, day_of_year
from services.bible.devotionals.blb import BlbDevotionalSource
from services.bible.devotionals.local import LocalDevotionalSource

log = logging.getLogger(__name__)

_SOURCES: tuple[str, ...] = (
    "blb",
    "local",
)


def sources() -> list[DevotionalSource]:
    """Build the source list from *current* runtime configuration.

    Config is read here rather than at import time on purpose: Identity's
    ``resolve_runtime_config()`` rewrites ``services.config`` globals at boot,
    and a devotional source that captured an unset base URL at import would stay
    permanently "not configured" until the next deploy.
    """
    import services.config as cfg

    built: list[DevotionalSource] = []
    for code in _SOURCES:
        if code == "blb":
            built.append(BlbDevotionalSource(cfg.BLB_BASE_URL or ""))
        elif code == "local":
            built.append(LocalDevotionalSource(cfg.BIBLE_DEVOTIONAL_DIR or ""))
    return sorted(built, key=lambda s: s.priority)


def describe_sources() -> list[dict]:
    return [source.describe() for source in sources()]


def daily(day: date | None = None, work: str | None = None) -> dict:
    """Today's devotional plus an account of every source that was skipped."""
    target = day or date.today()
    doy = day_of_year(target)
    chosen: DevotionalEntry | None = None
    chosen_source: DevotionalSource | None = None
    skipped: list[dict] = []

    for source in sources():
        if not source.configured():
            skipped.append({"source": source.code, "reason": source.unconfigured_reason()})
            continue
        candidates = (work,) if work else source.works
        for candidate in candidates:
            try:
                entry = source.entry_for(candidate, doy)
            except Exception as exc:
                # A broken source must be visible, not silently absent.
                log.warning("[Bible] devotional source %s failed: %s", source.code, exc)
                skipped.append({"source": source.code, "reason": f"{source.code} failed: {exc}"})
                break
            if entry is not None:
                chosen = entry
                chosen_source = source
                break
        if chosen is not None:
            break
        skipped.append(
            {
                "source": source.code,
                "reason": f"{source.code} has no entry for day {doy} of {source.works}",
            }
        )

    payload: dict = {
        "day": target.isoformat(),
        "day_of_year": doy,
        "entry": chosen.payload() if chosen else None,
        "source": chosen_source.code if chosen_source else None,
        "skipped": skipped,
    }
    if chosen is None:
        payload["reason"] = (
            "No devotional is available: "
            + "; ".join(item["reason"] for item in skipped)
            if skipped
            else "No devotional sources are registered"
        )
    return payload