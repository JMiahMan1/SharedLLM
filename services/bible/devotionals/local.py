# services/bible/devotionals/local.py
"""Devotionals from a local directory.

The escape hatch that keeps the feature from depending on anyone else's site:
an operator points ``BIBLE_DEVOTIONAL_DIR`` at a folder and drops in markdown,
one file per day. Every file is named ``NNNN.md`` (or ``NNNN.txt``) where NNNN
is the day of the year, so the same addressing as every online devotional
works.

Layout::

    $BIBLE_DEVOTIONAL_DIR/
        my_family/          <- a "work" is a subdirectory
            001.md          <- optional first line "# Title" and a "> Ref" line
            002.md
        fallback.md          <- files at the root are the "default" work

``> Reading: John 3:16`` on a line under the title sets the reference chip. Any
other content is the body.

The directory is optional. Unset is a visible warning the UI can show ("your
local devotionals folder isn't set"), never a silent empty card.
"""
from __future__ import annotations

import re
from pathlib import Path

from services.bible.devotionals.base import DevotionalEntry, DevotionalSource

_DAY_FILE = re.compile(r"^(?P<doy>\d{1,3})\.(?:md|txt)$")
_TITLE_LINE = re.compile(r"^#\s+(?P<title>.+?)\s*$")
_REFERENCE_LINE = re.compile(r"^>\s*(?:reading|ref(?:erence)?)\s*:\s*(?P<ref>.+?)\s*$", re.IGNORECASE)


class LocalDevotionalSource(DevotionalSource):
    code = "local"
    title = "Local devotionals folder"
    priority = 20

    def __init__(self, directory: str = "", work: str = "default"):
        self.directory = Path(directory).expanduser() if directory else None
        self.default_work = work
        # The registry iterates ``works`` when no work was requested, so the
        # root-level "default" work and any subdirectories have to be listed.
        self.works = (work,) + self.available_works()

    def configured(self) -> bool:
        if self.directory is None:
            return False
        return self.directory.is_dir()

    def unconfigured_reason(self) -> str:
        if self.directory is None:
            return (
                "Global setting 'bible_devotional_dir' is not set, so local "
                "devotionals are unavailable. Set it to a folder of NNNN.md files "
                "(one per day of the year) in Settings > Bible."
            )
        return f"Local devotionals directory does not exist: {self.directory}"

    def available_works(self) -> tuple[str, ...]:
        if not self.configured() or self.directory is None:
            return ()
        return tuple(sorted(child.name for child in self.directory.iterdir() if child.is_dir()))

    def _work_dir(self, work: str) -> Path | None:
        if self.directory is None:
            return None
        if work in {"", self.default_work}:
            return self.directory
        candidate = self.directory / work
        return candidate if candidate.is_dir() else None

    def entry_for(self, work: str, day_of_year: int) -> DevotionalEntry | None:
        if not self.configured():
            return None
        resolved = work or self.default_work
        folder = self._work_dir(resolved)
        if folder is None:
            return None
        for path in sorted(folder.glob("*")):
            match = _DAY_FILE.match(path.name)
            if not match or int(match.group("doy")) != day_of_year:
                continue
            title, reference, body = _split_document(path.read_text(encoding="utf-8"))
            return DevotionalEntry(
                source=self.code,
                work=resolved,
                title=title or f"Day {day_of_year}",
                day_of_year=day_of_year,
                kind="text",
                reference=reference,
                text=body,
            )
        return None


def _split_document(raw: str) -> tuple[str, str, str]:
    lines = raw.splitlines()
    title = ""
    reference = ""
    body_start = 0
    for index, line in enumerate(lines):
        title_match = _TITLE_LINE.match(line)
        if title_match:
            title = title_match.group("title")
            body_start = index + 1
            continue
        ref_match = _REFERENCE_LINE.match(line)
        if ref_match:
            reference = ref_match.group("ref")
            body_start = index + 1
            continue
        if line.strip():
            body_start = index
            break
    body = "\n".join(lines[body_start:]).strip()
    return title, reference, body