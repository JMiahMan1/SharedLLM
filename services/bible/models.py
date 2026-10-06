# services/bible/models.py
"""SQLModel tables for the Bible service.

Three very different kinds of data live here and they are kept apart on purpose:

1. **Corpus** (``BibleVersion`` / ``BibleEdition`` / ``BibleBook`` /
   ``BibleVerse`` / ``StudyNote`` / ``Devotional``) -- imported, immutable,
   shared by every user. Never written to on a request path; only the importer
   writes here.
2. **Reading state** (``UserBibleState`` / ``VerseMark`` / ``ReadingEvent`` /
   ``AchievementEarned``) -- per user, written on nearly every tap, and the
   only place a reader's own behaviour is recorded.

The corpus has **two axes, not one**. ``BibleVersion`` is the translation --
``nkjv`` is *the* New King James Version, imported exactly once, because two
copies of the same translation would drift apart. ``BibleEdition`` is a study
Bible published on top of that translation -- the Thomas Nelson NKJV Study
Bible, the MacArthur NKJV, a set of sermons, someone else's commentary. The
verse text is identical across editions of one translation, so
``BibleVerse`` is keyed on the translation alone and is never duplicated;
only ``StudyNote`` carries ``edition_code``. That is what lets a family read
NKJV from one study Bible on Sunday and another on Wednesday without either
one overwriting the other, and it is why ``import_notes()`` deletes by
``(version_code, edition_code)`` rather than by version.

Nothing here holds Scripture text the user wrote, a note body, or a quiz
answer the user should not see again: notes live in Nextcloud (they are Notes,
not Bible rows), and event payloads are shapes the API defines, not blobs.

The whole corpus + user state is one SQLite file at ``/data/bible.db`` so the
read path never needs a network round trip to the UI -- see
docs/BIBLE_STUDY.md "Reading is offline-first".
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import LargeBinary
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Now, in UTC, with its zone attached (sqlmodel refuses naive datetimes)."""
    return datetime.now(timezone.utc)


class BibleVersion(SQLModel, table=True):
    """One imported translation. ``code`` is the lowercase short id used in
    URLs and query params (``kjv``, ``asv``, ``web``, ``ylt``)."""

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    language: str = Field(default="en")
    # "public_domain" | "copyrighted" -- gates what the UI may offer and keeps
    # the licensing decision next to the text it applies to.
    license_class: str = Field(default="public_domain")
    # Verse count read at import time. A mismatch against reality is how a
    # truncated download announces itself instead of silently serving holes.
    verse_count: int = Field(default=0)
    imported_at: datetime = Field(default_factory=utcnow)


class BibleEdition(SQLModel, table=True):
    """One study Bible published on top of an imported translation.

    A translation answers "whose words are these"; an edition answers "who is
    explaining them". They are separate rows because a family will own more than
    one study Bible over the same translation -- two commentaries, a study Bible
    plus a set of sermons, the same notes in a second language -- and the text
    underneath is shared, not re-imported.

    ``code`` is the id used in query params (``nkjv-tmn``, ``nkjv-macarthur``)
    and is unique across the whole corpus, so an edition may be named without
    repeating the translation. Every installed translation gets at least one
    edition: the implicit ``<code>`` row meaning "text only, no study material",
    which is what makes "this translation has no notes" a question with an
    answer rather than a gap in the data.
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    version_code: str = Field(index=True)
    name: str
    publisher: str = Field(default="")
    language: str = Field(default="en")
    # A study Bible is separately licensed from the translation it explains, so
    # this repeats the licence rather than inheriting it.
    license_class: str = Field(default="public_domain")
    rights_holder: str = Field(default="")
    # Notes read at import time. Read back so the picker can say "12,055 notes"
    # without counting 44k rows per request.
    note_count: int = Field(default=0)
    note_kinds: str = Field(default="")
    imported_at: datetime = Field(default_factory=utcnow)


class BibleBook(SQLModel, table=True):
    """One book of one imported version.

    Books exist per version because a partial import is legitimate (an
    abridged or partial-language edition) and the reader must be able to show
    a book list that matches what it can actually serve.
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    version_code: str = Field(index=True)
    osis: str = Field(index=True)
    name: str
    order: int = Field(default=0)
    chapters: int = Field(default=0)


class BibleVerse(SQLModel, table=True):
    """A single verse. Primary key is the natural key -- there is no reason for
    an autoincrement id on an immutable row, and the natural key is what every
    query uses."""

    __table_args__ = {"extend_existing": True}
    version_code: str = Field(primary_key=True)
    osis: str = Field(primary_key=True)
    chapter: int = Field(primary_key=True)
    verse: int = Field(primary_key=True)
    text: str


class Devotional(SQLModel, table=True):
    """One day's devotional from one source.

    ``day_of_year`` is the addressing scheme every devotional on earth uses
    (``?doy=N``), which is what makes a day-addressable table work for
    year-round sources and for local files dropped into the devotionals
    directory.
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    work: str = Field(index=True)
    day_of_year: int = Field(index=True)
    title: str = Field(default="")
    reference: str = Field(default="")
    text: str = Field(default="")
    # Set when the entry is a pointer at another site rather than vendored
    # text (the BLB devotionals). One of "text" | "link".
    kind: str = Field(default="text")
    url: str = Field(default="")
    # Day of the month the entry starts at, for sources addressed by month/day
    # rather than day-of-year. 0 when not applicable.
    month: int = Field(default=0)
    day: int = Field(default=0)


class UserBibleState(SQLModel, table=True):
    """Per-user reading position and typographic preferences.

    One row per user, written whenever the reader moves. The UI reads this on
    cold start to open where the reader left off, which is the single most
    important feel in a reading app.
    """

    __table_args__ = {"extend_existing": True}
    username: str = Field(primary_key=True)
    last_book: str = Field(default="")
    last_chapter: int = Field(default=1)
    last_verse: int = Field(default=1)
    last_open_day: str = Field(default="")
    last_read_at: datetime | None = None
    default_version: str = Field(default="")
    default_edition: str = Field(default="")
    # The reader's own favourite, kept apart from the default so that "start me
    # where I left off" and "open my favourite" stay two different decisions.
    favorite_version: str = Field(default="")
    # The translation set beside the default one when the reader compares. Blank
    # means no comparison; it is never guessed, because quietly showing a second
    # version the reader did not ask for would make it unclear which words they
    # are reading.
    compare_version: str = Field(default="")
    # Whether study panels may show commentary published against another
    # translation. Off by default: such a note is commentary on different words.
    cross_version_notes: bool = Field(default=False)
    # Whether the chapter's commentary sits beside the text while reading, rather
    # than waiting behind a tap. Off by default so the reader starts as text.
    show_notes: bool = Field(default=False)
    font_scale: float = Field(default=1.0)
    line_height: float = Field(default=1.6)
    theme: str = Field(default="serif")
    read_aloud_voice: str = Field(default="")
    split_view: str = Field(default="compare")


class VerseMark(SQLModel, table=True):
    """A highlight or bookmark.

    ``note_path`` points at a note in Nextcloud -- the note *body* is not
    duplicated here (see docs/BIBLE_STUDY.md "one notes store").
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    version_code: str = Field(default="")
    osis: str = Field(index=True)
    chapter: int = Field(default=1)
    verse_start: int = Field(default=1)
    verse_end: int | None = None
    kind: str = Field(default="highlight")
    color: str = Field(default="")
    note_path: str = Field(default="")
    note_preview: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StudyNote(SQLModel, table=True):
    """Study material published alongside the text of one verse.

    Extracted separately from the verse text so reading is never slowed down by
    commentary, and keyed by the same canonical position the verse uses so the
    reader can ask "explain this verse" without a second lookup table. ``verse``
    is 0 for material that belongs to the whole chapter.

    ``edition_code`` is what makes several study Bibles of the same translation
    coexist: two rows with the same position and kind are two different
    commentaries, not a duplicate, so this column is part of a note's identity
    rather than a label on it.
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    version_code: str = Field(index=True)
    edition_code: str = Field(default="", index=True)
    osis: str = Field(index=True)
    chapter: int = Field(default=1)
    verse: int = Field(default=0)
    kind: str = Field(default="commentary", index=True)
    ordinal: int = Field(default=0)
    body: str = Field(default="")
    source: str = Field(default="")
    imported_at: datetime = Field(default_factory=utcnow)


class NarrationAudio(SQLModel, table=True):
    """A spoken rendering of one passage, kept so it is only synthesised once.

    ``key`` is a digest of translation, reference and voice (see
    ``narration.cache_key``), which is the whole identity: the same passage in
    the same voice always sounds the same, so there is nothing to invalidate
    except the corpus itself. ``audio`` holds the WAV bytes as SQLite stores
    them -- a chapter is a couple of megabytes, which is far cheaper than
    re-running the speech engine every time somebody presses Play.
    """

    __table_args__ = {"extend_existing": True}
    key: str = Field(primary_key=True)
    version_code: str = Field(default="", index=True)
    reference: str = Field(default="")
    voice: str = Field(default="")
    verse_count: int = Field(default=0)
    mime_type: str = Field(default="audio/wav")
    audio: bytes = Field(default=b"", sa_type=LargeBinary)
    created_at: datetime = Field(default_factory=utcnow)


class ImportRun(SQLModel, table=True):
    """One attempt to install a translation or a study Bible.

    The refusals are the interesting half. "Extracted 2 of 66 books, so nothing
    was written" and "the checksum does not match the manifest" are answers the
    operator has to be able to find after the fact, so failed runs are recorded
    exactly like successful ones. ``log`` is the newline-joined transcript the
    admin page shows; ``status`` is ``succeeded`` or ``failed``.

    ``source`` names where the bytes came from -- a path, an upload, or
    ``provider:id`` -- so the same translation installed two ways is
    distinguishable. No scripture text is stored here.
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="", index=True)
    kind: str = Field(default="json", index=True)
    code: str = Field(default="", index=True)
    name: str = Field(default="")
    provider: str = Field(default="")
    provider_id: str = Field(default="")
    status: str = Field(default="", index=True)
    message: str = Field(default="")
    verse_count: int = Field(default=0)
    book_count: int = Field(default=0)
    note_count: int = Field(default=0)
    log: str = Field(default="")
    duration_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class ReadingEvent(SQLModel, table=True):
    """One thing the reader did.

    Metadata only: the kind, a reference, and a small numeric value. Never the
    text of a chapter read, a note written, or a question asked. The consent
    boundary is the same one Identity's ``FeatureUsage`` enforces, and the
    events themselves are opt-in sharing (``UserActivitySharing``).
    """

    __table_args__ = {"extend_existing": True}
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True)
    kind: str = Field(index=True)
    ref: str = Field(default="")
    day: str = Field(index=True)
    value: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class AchievementEarned(SQLModel, table=True):
    """The bank of achievements a user has earned.

    Definitions stay in ``bible_achievements.json`` and progress stays derived
    (see ``achievements.py``, which mirrors services/geo/achievements.py); this
    table exists only to make ``earned_on`` stable, so a user cannot lose a
    badge because a rule's inputs were re-evaluated.
    """

    __table_args__ = {"extend_existing": True}
    username: str = Field(primary_key=True)
    achievement_id: str = Field(primary_key=True)
    points: int = Field(default=0)
    earned_on: date = Field(default_factory=date.today)