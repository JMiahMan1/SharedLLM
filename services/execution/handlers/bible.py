"""Five read-only actions over the family's Bible service.

The action set is closed to reads -- ``read``, ``search``, ``study_notes``,
``votd``, ``catalogue`` -- because this codebase has no approval gate anywhere
in the tool path, and because reading position, marks and achievements belong
to the person holding the phone: an agent writing them would silently
overwrite a human's place in a book. Not offering the verb at all is the only
control that holds; the Pydantic ``Literal`` refuses the rest at the schema
edge.

Everything interesting is delegated: the Bible service owns the corpus, the
reference parser and the study editions. This layer turns their refusals into
``ExecutionResult`` failures the model can read, never swallows them, and
bounds its own message so a whole Psalm cannot flood a tool result.
"""

from __future__ import annotations

import logging

from services.execution.bible_client import (
    BibleClient,
    BibleRefusal,
    BibleUnavailable,
)
from services.execution.schemas import BibleRequest, ExecutionResult

log = logging.getLogger("execution.bible")

SERVICE = "bible"

_MESSAGE_ROWS = 10
_MESSAGE_CHARS = 6000
_VERSE_TEXT_CHARS = 300


def _client() -> BibleClient:
    from services.config import BIBLE_SVC_URL, INTERNAL_SECRET, NETWORK_MODE

    base_url = BIBLE_SVC_URL
    if NETWORK_MODE == "host" and "://bible:" in str(base_url):
        raise BibleUnavailable(
            "bible_svc_url still points at the Docker service name "
            f"'bible' ({base_url}) while execution runs with host networking, "
            "so it cannot be resolved. Set HOST_BIBLE_SVC_URL to the "
            "published address, for example http://localhost:8010."
        )
    return BibleClient(base_url=base_url, internal_secret=INTERNAL_SECRET)


async def handle_bible(req: BibleRequest) -> ExecutionResult:
    """Dispatch one read action, converting every refusal into a visible failure."""
    try:
        client = _client()
        if req.action == "read":
            return await _read(client, req)
        if req.action == "search":
            return await _search(client, req)
        if req.action == "study_notes":
            return await _study_notes(client, req)
        if req.action == "votd":
            return await _votd(client, req)
        if req.action == "catalogue":
            return await _catalogue(client, req)
        return ExecutionResult.fail(
            f"Action {req.action!r} is not supported. The Bible surface is "
            "read-only: read, search, study_notes, votd, catalogue.",
            service=SERVICE,
        )
    except (BibleRefusal, BibleUnavailable) as exc:
        return ExecutionResult.fail(str(exc), service=SERVICE)
    except Exception as exc:  # noqa: BLE001 - a tool must answer, not 500
        log.exception("Bible %s failed for user %s", req.action, req.user_context.user)
        return ExecutionResult.fail(
            f"Bible {req.action} failed unexpectedly: {exc}", service=SERVICE
        )


def _clip(text: str) -> str:
    """Bound a tool message honestly, naming what was cut and where to look."""
    if len(text) <= _MESSAGE_CHARS:
        return text
    return (
        text[:_MESSAGE_CHARS]
        + f"\n\n[Truncated at {_MESSAGE_CHARS:,} characters. Ask for a narrower "
        "reference or fewer results to see the rest.]"
    )


def _verse_lines(verses: list[dict], *, text_chars: int | None = None) -> list[str]:
    lines = []
    for verse in verses:
        text = str(verse.get("text") or "")
        if text_chars is not None and len(text) > text_chars:
            text = text[:text_chars] + "…"
        lines.append(f"{verse.get('reference') or verse.get('osis')} {text}".strip())
    return lines


async def _read(client: BibleClient, req: BibleRequest) -> ExecutionResult:
    ref = str(req.ref or "").strip()
    if not ref:
        return ExecutionResult.fail(
            "action=read needs a ref naming the passage to read "
            "(for example: John 3:16 or Psalm 23).",
            service=SERVICE,
        )
    body = await client.read(ref=ref, version=req.version)
    verses = list(body.get("verses") or [])
    if not verses:
        note = body.get("reference") or ref
        return ExecutionResult.ok(
            f"{note} has no verses in this translation. Use catalogue to see "
            "the installed translations, or try another reference.",
            service=SERVICE,
            detail={"reference": note, "count": 0},
        )
    header = f"{body.get('reference') or ref} ({body.get('version')}) — {len(verses)} verses:"
    message = _clip("\n".join([header, "", *_verse_lines(verses)]))
    return ExecutionResult.ok(
        message,
        service=SERVICE,
        detail={
            "reference": body.get("reference"),
            "version": body.get("version"),
            "count": len(verses),
        },
    )


async def _search(client: BibleClient, req: BibleRequest) -> ExecutionResult:
    query = str(req.q or "").strip()
    if len(query) < 2:
        return ExecutionResult.fail(
            "action=search needs q with at least 2 characters (for example: "
            "shepherd, or love thy neighbour).",
            service=SERVICE,
        )
    body = await client.search(
        query=query, version=req.version, book=req.book, limit=req.limit
    )
    results = list(body.get("results") or [])
    if not results:
        where = f" in {req.book}" if req.book else ""
        return ExecutionResult.ok(
            f"No verses match {query!r}{where}"
            + (f" in {body.get('version')}" if body.get("version") else "")
            + ".",
            service=SERVICE,
            detail={"query": query, "count": 0, "results": []},
        )
    shown = _verse_lines(results[:_MESSAGE_ROWS], text_chars=_VERSE_TEXT_CHARS)
    total = int(body.get("count") or len(results))
    hidden = total - len(shown)
    more = f" ({hidden:,} more not shown; narrow the query or raise limit)" if hidden > 0 else ""
    message = _clip(
        f"{total:,} verses match {query!r} in {body.get('version')}:\n"
        + "\n".join(shown)
        + more
    )
    return ExecutionResult.ok(
        message,
        service=SERVICE,
        detail={"query": query, "count": total, "results": results[:_MESSAGE_ROWS]},
    )


async def _study_notes(client: BibleClient, req: BibleRequest) -> ExecutionResult:
    ref = str(req.ref or "").strip()
    if not ref:
        return ExecutionResult.fail(
            "action=study_notes needs a ref naming the passage to comment on "
            "(for example: Romans 1:16).",
            service=SERVICE,
        )
    body = await client.study_notes(
        ref=ref,
        version=req.version,
        edition=req.edition,
        kind=req.kind,
        cross_version=req.cross_version,
    )
    notes = list(body.get("notes") or [])
    if not notes:
        explanation = str(body.get("note") or "").strip()
        return ExecutionResult.ok(
            explanation
            or f"No study notes for {body.get('reference') or ref} in this edition.",
            service=SERVICE,
            detail={
                "reference": body.get("reference"),
                "edition": body.get("edition"),
                "count": 0,
            },
        )
    edition_name = str(body.get("edition_name") or body.get("edition") or "study Bible")
    shown = [
        f"[{note.get('kind')}] {note.get('reference')}: "
        f"{str(note.get('body') or '')[:_VERSE_TEXT_CHARS]}"
        for note in notes[:_MESSAGE_ROWS]
    ]
    hidden = len(notes) - len(shown)
    more = f"\n({hidden:,} more notes not shown; ask for a narrower passage)" if hidden else ""
    message = _clip(
        f"{len(notes)} notes from {edition_name} on "
        f"{body.get('reference') or ref}:\n" + "\n".join(shown) + more
    )
    return ExecutionResult.ok(
        message,
        service=SERVICE,
        detail={
            "reference": body.get("reference"),
            "edition": body.get("edition"),
            "edition_name": edition_name,
            "count": len(notes),
        },
    )


async def _votd(client: BibleClient, req: BibleRequest) -> ExecutionResult:
    body = await client.verse_of_day(day=req.day, version=req.version, scope=req.scope)
    text = str(body.get("text") or "").strip()
    if not text:
        return ExecutionResult.fail(
            "The verse of the day returned no text for "
            f"{body.get('day') or req.day or 'today'}; try catalogue to pick "
            "an installed translation explicitly.",
            service=SERVICE,
        )
    message = (
        f"{body.get('reference')} ({body.get('version')}, {body.get('day')}): {text}"
    )
    return ExecutionResult.ok(
        _clip(message),
        service=SERVICE,
        detail={
            "reference": body.get("reference"),
            "version": body.get("version"),
            "day": body.get("day"),
        },
    )


async def _catalogue(client: BibleClient, req: BibleRequest) -> ExecutionResult:
    body = await client.catalogue(version=req.version)
    versions_body = body.get("versions") or {}
    editions_body = body.get("editions") or {}
    books_body = body.get("books") or {}
    versions = list(versions_body.get("versions") or [])
    installed = [entry for entry in versions if entry.get("installed")]
    installed_line = (
        "; ".join(
            f"{entry.get('code')} ({entry.get('name')})" for entry in installed
        )
        or "none installed"
    )
    resolved = str(editions_body.get("version") or req.version or "")
    editions = list(editions_body.get("editions") or [])
    editions_line = (
        "; ".join(
            f"{entry.get('code')} ({entry.get('name')}, "
            f"{entry.get('note_count') or 0} notes"
            + ("" if entry.get("installed") else ", not installed")
            + ")"
            for entry in editions[:_MESSAGE_ROWS]
        )
        or "none"
    )
    books = list(books_body.get("books") or [])
    message = _clip(
        f"Installed translations: {installed_line}. "
        f"Study editions for {resolved or 'the default translation'}: {editions_line}. "
        f"{len(books)} books in {books_body.get('version') or resolved or 'the default'}."
        + (f" {versions_body.get('message')}" if versions_body.get("message") else "")
    )
    return ExecutionResult.ok(
        message,
        service=SERVICE,
        detail={
            "installed": [entry.get("code") for entry in installed],
            "editions": [entry.get("code") for entry in editions],
            "books": len(books),
        },
    )
