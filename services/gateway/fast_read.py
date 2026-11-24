"""Spoken "Read ..." requests on the fast path: scripture, a library book, and "keep reading".

"Read John 3:16" and "Read chapter 1 of <Title>" were classified as media and sent
to an audiobook search for the words "John 3:16". Here they are read aloud instead, the
voice reply being the reading:

* **Scripture.** A reference with a chapter ("John 3:16", "Psalm 23", "first Corinthians
  thirteen", "John chapter 3 verse 16") is read from the Bible service (execution's
  BibleRequest ``read``). A bare book name ("Read Job") is not scripture: it may be a
  book on the shelf.
* **A chapter of a library book.** "Read chapter 1 of <Title> [by <Author>]" fetches that
  chapter from the Calibre library (CalibreRequest ``fetch_text``) in voice-sized parts.
* **A library book.** "Read <Title>" reads the book from its first chapter when the
  library holds it; otherwise the request falls through to the media path (an audiobook
  of that title, as before).
* **Keep reading.** "Keep reading", "read the next part", "read the next chapter" carry on
  from the last thing read for this person (kept for ``READ_STATE_TTL_S``); with nothing
  read recently they fall through ("keep reading" then resumes an audiobook, as before).

"Read my messages", "read the news", "read this article" and the like are other jobs
(``reads_something_else``) and never come here.

Nothing here names a book or a household: titles come from the request and the library.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("gateway.fast_read")

#: How long "keep reading" remembers what was read last.
READ_STATE_TTL_S = 12 * 3600
#: A title, a reference or "chapter N of <title> by <author>" is at most this many words.
MAX_OBJECT_WORDS = 14
#: Characters of a book read per voice turn (about seven minutes of speech).
VOICE_PART_CHARS = 6000

_VERB = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)?(?:read|reed)\b(?:\s+(?:me|us|aloud|out\s+loud|to\s+(?:me|us)))*"
    r"[\s,]*", re.IGNORECASE)
_TAIL = re.compile(r"(?:[\s,]+(?:please|for\s+(?:me|us)|now|thanks|thank\s+you|aloud|out\s+loud))+$", re.IGNORECASE)

#: What "read" means when it is not a book or scripture: a message, the news, a page, a list.
_ELSEWHERE = re.compile(
    r"^(?:(?:my|our|the|this|that|these|those|his|her|their|your|any|new|unread|latest|last|today'?s|recent)\s+)*"
    r"(?:e-?mails?|messages?|texts?|notifications?|news|headlines|articles?|page|website|web\s*page|link|"
    r"it|this|that|them|notes?|list|calendar|schedule|agenda|reminders?|recipes?|weather|forecast|"
    r"screen|label|sign|letter|mail|inbox|chat|conversation|story\s+you\s+wrote|what\s+(?:it|this|that)\s+says)\b",
    re.IGNORECASE,
)


def object_of(query: str) -> str:
    """The words after "read (me)": "Read me chapter 1 of X, please" -> "chapter 1 of X"."""
    text = (query or "").strip().strip("?.!").strip()
    text = re.sub(r"^(?:(?:hey|ok|okay)\s+)?jarvis[\s,.!]*", "", text, flags=re.IGNORECASE)
    m = _VERB.match(text)
    if not m:
        return ""
    return _TAIL.sub("", text[m.end():]).strip(" ,.!?\"'")


def reads_something_else(query: str) -> bool:
    """True for "read my messages", "read the news", "read this": not a book or scripture."""
    obj = object_of(query)
    return bool(obj) and bool(_ELSEWHERE.match(obj))


# --------------------------------------------------------------------------- #
# Scripture                                                                    #
# --------------------------------------------------------------------------- #

_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_ORDINAL_BOOK = {"first": "1", "second": "2", "third": "3", "1st": "1", "2nd": "2", "3rd": "3", "i": "1",
                 "ii": "2", "iii": "3"}


def _spoken_numbers(text: str) -> str:
    """"one hundred nineteen" -> "119", "twenty three" -> "23"; other words unchanged."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i].lower().strip(",")
        if w in _UNITS or w in _TENS or (w == "hundred" and out and out[-1].isdigit()):
            total = 0
            j = i
            if w == "hundred":
                total = int(out.pop()) * 100
                j = i + 1
            while j < len(words):
                v = words[j].lower().strip(",").replace("-", " ")
                parts = v.split()
                if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS:
                    total += _TENS[parts[0]] + _UNITS[parts[1]]
                elif v in _TENS:
                    total += _TENS[v]
                elif v in _UNITS:
                    total += _UNITS[v]
                elif v == "hundred":
                    total = (total or 1) * 100
                elif v == "and" and total >= 100:
                    pass
                else:
                    break
                j += 1
            out.append(str(total))
            i = j
            continue
        out.append(words[i])
        i += 1
    return " ".join(out)


def spoken_reference(text: str) -> str:
    """A reference as written from one as said: "first John chapter four verses seven to twelve" -> "1 John 4:7-12"."""
    t = " ".join((text or "").replace(chr(0x2013), "-").split())
    t = re.sub(r"^(?:the\s+)?(?:book\s+of\s+)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(?:the\s+)?(?:gospel\s+(?:of|according\s+to)\s+)(?:saint\s+|st\.?\s+)?", "", t, flags=re.IGNORECASE)
    first = t.split(" ", 1)
    if len(first) == 2 and first[0].lower() in _ORDINAL_BOOK:
        t = f"{_ORDINAL_BOOK[first[0].lower()]} {first[1]}"
    t = _spoken_numbers(t)
    t = re.sub(r"\s+chapter\s+(\d+)", r" \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+verses?\s+(\d+)", r":\1", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s+(?:to|through|thru)\s+(\d)", r"\1-\2", t, flags=re.IGNORECASE)
    t = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", t)
    t = re.sub(r"(\d)\s+:\s*(\d)", r"\1:\2", t)
    return t.strip(" ,.")


@dataclass(frozen=True)
class ScriptureRef:
    text: str       # the reference as the Bible service reads it ("John 3:16")
    book: str       # its book (OSIS id)
    chapter: int    # its first chapter


def scripture_ref(words: str) -> ScriptureRef | None:
    """The passage ``words`` names, when it names one with a chapter; else None."""
    candidate = spoken_reference(words)
    if not candidate or not re.search(r"\d", candidate.split(" ", 1)[-1]):
        return None
    try:
        from services.bible.refs import parse_reference

        spans = parse_reference(candidate)
    except Exception:
        return None
    if not spans or any(getattr(s, "whole_book", False) for s in spans):
        return None
    return ScriptureRef(candidate, spans[0].book, int(spans[0].chapter_start))


# --------------------------------------------------------------------------- #
# What a read request asks for                                                 #
# --------------------------------------------------------------------------- #

_CONTINUE = re.compile(
    r"^(?:(?:keep|continue|carry\s+on|go\s+on)\s+(?:reading|with\s+(?:the\s+)?(?:book|chapter|reading))|"
    r"(?:read|reed)\s+(?:on|more|some\s+more|the\s+next\s+(?:part|bit|section|page|chapter)|next\s+(?:part|chapter))|"
    r"(?:continue|keep\s+going\s+with)\s+(?:the|my)\s+(?:book|chapter|reading))$",
    re.IGNORECASE,
)
_NEXT_CHAPTER = re.compile(r"\bnext\s+chapter\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReadAsk:
    kind: str                     # "scripture" | "chapter" | "book" | "continue"
    scripture: ScriptureRef | None = None
    chapter: int | None = None
    title: str = ""
    author: str = ""
    next_chapter: bool = False


def _plain(query: str) -> str:
    text = (query or "").strip().strip("?.!").strip()
    text = re.sub(r"^(?:(?:hey|ok|okay)\s+)?jarvis[\s,.!]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)", "", text, flags=re.IGNORECASE)
    return _TAIL.sub("", text).strip(" ,.!?")


def parse(query: str) -> ReadAsk | None:
    """What a "read ..." (or "keep reading") request asks for, or None when it is not one for this module."""
    plain = _plain(query)
    if _CONTINUE.match(plain):
        return ReadAsk("continue", next_chapter=bool(_NEXT_CHAPTER.search(plain)))
    obj = object_of(query)
    if not obj or reads_something_else(query) or "\n" in (query or "") or len(obj.split()) > MAX_OBJECT_WORDS:
        return None  # "Read the following code and ..." is a chat request, not a title
    ref = scripture_ref(obj)
    if ref is not None:
        return ReadAsk("scripture", scripture=ref)
    from services.gateway import library_names

    chapter = library_names.chapter_named(f"read {obj}")
    if chapter is not None and chapter.title:
        return ReadAsk("chapter", chapter=chapter.chapter, title=chapter.title, author=chapter.author)
    if re.search(r"\b(?:chapter|verse)\b", obj, re.IGNORECASE):
        return None  # a chapter of something not named well enough: the model asks
    title = re.sub(r"^(?:the\s+book\s+|the\s+novel\s+|the\s+story\s+|a\s+story\s+called\s+)", "", obj, flags=re.IGNORECASE)
    title = re.sub(r"\s+(?:to\s+(?:me|us))$", "", title, flags=re.IGNORECASE)
    by = re.match(r"^(?P<title>.+?)\s+by\s+(?P<author>.+)$", title, re.IGNORECASE)
    if by:
        return ReadAsk("book", title=by.group("title").strip(), author=by.group("author").strip())
    if re.fullmatch(r"(?:a|an|some|something|anything|a\s+story|a\s+book|stories|books?)", title, re.IGNORECASE):
        return None  # "read me a story": the model picks one
    return ReadAsk("book", title=title.strip())


# --------------------------------------------------------------------------- #
# Reading                                                                      #
# --------------------------------------------------------------------------- #

Post = Callable[[str, dict], Awaitable[dict]]


def _state_key(user_id: str) -> str:
    return f"read:last:{user_id}"


def remember(user_id: str, state: dict) -> None:
    from services.gateway.cache import redis_cache_set

    redis_cache_set(_state_key(user_id), json.dumps(state), READ_STATE_TTL_S)


def last_read(user_id: str) -> dict | None:
    from services.gateway.cache import redis_cache_get

    raw = redis_cache_get(_state_key(user_id))
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _verse_text(message: str) -> str:
    """The verses of a BibleRequest answer as one passage to say, without each verse's reference."""
    _head, _sep, body = (message or "").partition("\n\n")
    lines = [re.sub(r"^.*?\b\d+:\d+\s+", "", ln, count=1).strip() for ln in body.splitlines() if ln.strip()]
    return " ".join(ln for ln in lines if ln and not ln.startswith("[Truncated"))


async def read_scripture(ref: ScriptureRef, user_context: dict, post: Post, user_id: str) -> str:
    body = await post("/execute/bible", {"action": "read", "ref": ref.text, "user_context": user_context})
    message = str(body.get("message") or "")
    if body.get("status") != "SUCCESS":
        return message or f"I couldn't read {ref.text}."
    detail = body.get("detail") or {}
    if not detail.get("count"):
        return message
    remember(user_id, {"kind": "scripture", "book": ref.book, "chapter": ref.chapter,
                       "reference": str(detail.get("reference") or ref.text)})
    version = str(detail.get("version") or "").upper()
    head = str(detail.get("reference") or ref.text) + (f", {version}" if version else "")
    return f"{head}. {_verse_text(message)}"


async def _fetch_part(post: Post, user_context: dict, *, chapter: int, part: int, title: str = "",
                      author: str = "", book_id: Any = None) -> dict:
    payload: dict[str, Any] = {"action": "fetch_text", "chapter": str(chapter), "part": part,
                               "max_chars": VOICE_PART_CHARS, "user_context": user_context}
    if book_id not in (None, ""):
        payload["book_id"] = int(book_id)
    else:
        payload["query"] = " ".join(p for p in (title, author) if p)
    return await post("/execute/calibre", payload)


def _spoken_part(body: dict) -> tuple[str, dict]:
    """(what to say, the state to remember) from a CalibreRequest chapter answer."""
    detail = body.get("detail") or {}
    chapter = detail.get("chapter") or {}
    message = str(body.get("message") or "")
    _head, sep, text = message.partition("\n\n")
    text = text if sep else message
    title = str(detail.get("title") or "the book")
    author = str(detail.get("author") or "").strip()
    number = chapter.get("number")
    name = str(chapter.get("title") or "").strip()
    part, parts = int(chapter.get("part") or 1), int(chapter.get("parts") or 1)
    label = f"chapter {number}" if number is not None else (name or "the opening")
    if name and number is not None and name.lower() != f"chapter {number}".lower():
        label += f", {name}"
    asked = str(detail.get("asked_for") or "").strip()
    intro = (f"I don't have {asked}; the closest is {title}. " if asked else "")
    if part == 1:
        intro += f"{title}{' by ' + author if author else ''}, {label}. "
    else:
        intro += f"{label}, continued. "
    if part < parts:
        outro = f" That's part {part} of {parts} of this chapter. Say 'keep reading' for more."
    else:
        outro = " That's the end of the chapter. Say 'keep reading' for the next one."
    state = {"kind": "book", "book_id": detail.get("book_id"), "title": title, "author": author,
             "chapter": number, "part": part, "parts": parts}
    return intro + text.strip() + outro, state


async def read_book_chapter(post: Post, user_context: dict, user_id: str, *, chapter: int, part: int = 1,
                            title: str = "", author: str = "", book_id: Any = None) -> str:
    body = await _fetch_part(post, user_context, chapter=chapter, part=part, title=title, author=author,
                             book_id=book_id)
    if body.get("status") != "SUCCESS":
        return str(body.get("message") or f"I couldn't read chapter {chapter} of {title or 'that book'}.")
    said, state = _spoken_part(body)
    remember(user_id, state)
    return said


async def find_book(post: Post, user_context: dict, title: str, author: str = "") -> dict | None:
    """The library book a spoken title names (by the same rules as playing), or None."""
    from services.common.media_titles import title_matches

    body = await post("/execute/calibre", {"action": "search", "query": " ".join(p for p in (title, author) if p),
                                           "limit": 10, "user_context": user_context})
    books = (body.get("detail") or {}).get("books") or [] if body.get("status") == "SUCCESS" else []
    if not books and author:
        body = await post("/execute/calibre", {"action": "search", "query": title, "limit": 10,
                                               "user_context": user_context})
        books = (body.get("detail") or {}).get("books") or [] if body.get("status") == "SUCCESS" else []
    for book in books:
        if title_matches(title, str(book.get("title") or "")):
            return book
    return None


async def continue_reading(ask: ReadAsk, post: Post, user_context: dict, user_id: str) -> str | None:
    """The next part (or chapter) of what was read last, or None when nothing was read recently."""
    state = last_read(user_id)
    if not state:
        return None
    if state.get("kind") == "scripture":
        ref = f"{state.get('book')} {int(state.get('chapter') or 0) + 1}"
        parsed = scripture_ref(ref)
        if parsed is None:
            return "That was the last chapter of the book."
        return await read_scripture(parsed, user_context, post, user_id)
    chapter = state.get("chapter")
    if chapter is None:
        return None
    part, parts = int(state.get("part") or 1), int(state.get("parts") or 1)
    if not ask.next_chapter and part < parts:
        nxt_chapter, nxt_part = int(chapter), part + 1
    else:
        nxt_chapter, nxt_part = int(chapter) + 1, 1
    body = await _fetch_part(post, user_context, chapter=nxt_chapter, part=nxt_part, book_id=state.get("book_id"),
                             title=str(state.get("title") or ""), author=str(state.get("author") or ""))
    if body.get("status") != "SUCCESS":
        if nxt_part == 1:
            return f"That was the last chapter of {state.get('title') or 'the book'}."
        return str(body.get("message") or "I couldn't read the next part.")
    said, new_state = _spoken_part(body)
    remember(user_id, new_state)
    return said


async def handle(ask: ReadAsk, post: Post, user_context: dict, user_id: str) -> str | None:
    """The spoken reading for ``ask``, or None when the request belongs to the media path after all."""
    if ask.kind == "continue":
        return await continue_reading(ask, post, user_context, user_id)
    if ask.kind == "scripture" and ask.scripture is not None:
        return await read_scripture(ask.scripture, user_context, post, user_id)
    if ask.kind == "chapter" and ask.chapter:
        return await read_book_chapter(post, user_context, user_id, chapter=ask.chapter, title=ask.title,
                                       author=ask.author)
    if ask.kind == "book" and ask.title:
        book = await find_book(post, user_context, ask.title, ask.author)
        if book is None:
            log.info(f"[FastRead] '{ask.title}' is not in the book library; trying the media sources")
            return None
        return await read_book_chapter(post, user_context, user_id, chapter=1, book_id=book.get("book_id"),
                                       title=str(book.get("title") or ask.title))
    return None
