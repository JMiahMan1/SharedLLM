"""Read scripture through the Bible service.

The Bible service owns the corpus, the reference parser, the study editions
and the verse-of-the-day pick; this client is only a typed caller. Everything
goes over the internal network with the internal secret, exactly like the
Calibre client goes through storage, so there is one place that knows how to
talk to each upstream.

Read-only by construction: there is no method here that writes anything.
Reading position, marks and achievements belong to the person holding the
phone, and this codebase has no approval gate anywhere in the tool path, so
an irreversible verb must not be nameable by a model. The Pydantic ``Literal``
on the request schema refuses the rest at the edge; this module simply never
offers them.

Bible 4xx answers are teaching messages (``"Unknown book"`` with the options,
``"…has no verses in version X"``), so they surface as ``BibleRefusal`` -- the
caller asked for something the corpus cannot give, and the detail is the fix.
Transport failures and 5xx are ``BibleUnavailable``: an operator problem.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.execution import http_client

log = logging.getLogger("execution.bible")

READ_TIMEOUT_SECONDS = 20.0
CATALOGUE_TIMEOUT_SECONDS = 10.0


class BibleRefusal(ValueError):
    """The caller asked for something the corpus cannot answer (4xx detail)."""


class BibleUnavailable(RuntimeError):
    """The Bible service could not be reached or answered 5xx (failed)."""


class BibleClient:
    """Five reads over the Bible service: read, search, study notes, VOTD, catalogue."""

    def __init__(self, *, base_url: str, internal_secret: str) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.internal_secret = str(internal_secret or "")

    def _endpoint(self) -> str:
        if not self.base_url:
            raise BibleUnavailable(
                "The Bible service URL is not set, so scripture cannot be "
                "read. Set bible_svc_url (or BIBLE_SVC_URL / "
                "HOST_BIBLE_SVC_URL for host networking)."
            )
        if not self.internal_secret:
            raise BibleUnavailable(
                "The internal secret is not set, so the Bible service will "
                "refuse the request. Set INTERNAL_SECRET."
            )
        return self.base_url

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self.internal_secret}

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, timeout: float = READ_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        url = f"{self._endpoint()}{path}"
        clean = {
            key: value
            for key, value in (params or {}).items()
            if value is not None and value != ""
        }
        try:
            resp = await http_client.request(
                "GET", url, params=clean or None, headers=self._headers(), timeout=timeout
            )
        except Exception as exc:
            raise BibleUnavailable(
                f"The Bible service at {url} could not be reached: {exc}"
            ) from exc
        return _decode(resp, url)

    async def read(self, *, ref: str, version: str | None = None) -> dict[str, Any]:
        return await self._get("/passages", {"ref": ref, "version": version})

    async def search(
        self,
        *,
        query: str,
        version: str | None = None,
        book: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return await self._get(
            "/search",
            {"q": query, "version": version, "book": book, "limit": limit},
        )

    async def study_notes(
        self,
        *,
        ref: str,
        version: str | None = None,
        edition: str | None = None,
        kind: str | None = None,
        cross_version: bool = False,
    ) -> dict[str, Any]:
        return await self._get(
            "/study/notes",
            {
                "ref": ref,
                "version": version,
                "edition": edition,
                "kind": kind,
                "cross_version": "true" if cross_version else None,
            },
        )

    async def verse_of_day(
        self, *, day: str | None = None, version: str | None = None, scope: str = "all"
    ) -> dict[str, Any]:
        return await self._get(
            "/verse-of-day", {"day": day, "version": version, "scope": scope}
        )

    async def catalogue(self, *, version: str | None = None) -> dict[str, Any]:
        """Translations, study editions and book list in one call.

        ``/editions`` and ``/books`` resolve the default translation when no
        version is given, so all three fetches work either way; they are run
        sequentially because each is cheap and a partial answer would leave the
        caller guessing which part failed.
        """
        versions = await self._get("/versions", timeout=CATALOGUE_TIMEOUT_SECONDS)
        editions = await self._get(
            "/editions", {"version": version}, timeout=CATALOGUE_TIMEOUT_SECONDS
        )
        books = await self._get(
            "/books", {"version": version}, timeout=CATALOGUE_TIMEOUT_SECONDS
        )
        return {"versions": versions, "editions": editions, "books": books}


def _decode(resp: dict[str, Any], url: str) -> dict[str, Any]:
    status = int(resp.get("status_code") or 0)
    text = resp.get("text") or ""
    if status >= 400:
        detail = text.strip()
        try:
            parsed = json.loads(text)
            raw = parsed.get("detail", detail)
            if isinstance(raw, list):
                detail = "; ".join(
                    str(item.get("msg") or item) if isinstance(item, dict) else str(item)
                    for item in raw
                )
            else:
                detail = str(raw)
        except ValueError:
            pass
        message = f"{url} answered {status}" + (f": {detail[:400]}" if detail else ".")
        if 400 <= status < 500:
            raise BibleRefusal(detail[:400] or message)
        raise BibleUnavailable(message)
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise BibleUnavailable(f"{url} did not answer with JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise BibleUnavailable(f"{url} answered with {type(body).__name__}, not an object.")
    return body
