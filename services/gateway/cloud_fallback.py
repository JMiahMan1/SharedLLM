"""An online model for a few named features, only while the local AI is offline.

The household's rule: anything sensitive (Guardian's photo and screenshot checks,
content monitoring, app vetting, unlocks, chat, Raven, anything with personal
data) runs on the local model or not at all. A few features carry nothing
personal: a question about a Bible passage is the passage, its public text, the
translation, the question and a reading level. For those, and only while the
local AI's breaker is open, an online model may answer instead of "not now".

Everything that decides what may leave the house is in this module:

* ``FEATURES`` names every feature that has an online template. The allowlist
  setting ``cloud_fallback_features`` may only list names from it; anything
  else stops the gateway at startup with the name in the message.
* ``BibleStudyRequest`` is the whole outbound payload for the Bible feature.
  The route builds it from a strict body (unknown fields are refused, not
  dropped), and ``CloudFallbackClient.answer`` accepts that exact type and
  nothing else. The prompt is rendered from its fields alone, so there is no
  path for a name, a note, a highlight, history or memory to reach the provider.
* ``CloudFallbackClient.answer`` refuses (raises ``CloudFallbackRefused`` and
  logs the reason once) a feature that is not allowlisted, a payload of any
  other type, a call while the local AI is up, and a call with no provider.

The provider is the household's existing online provider settings
(``llm_cloud_url``, ``llm_cloud_api_key``) and the model is
``cloud_fallback_model`` (meant to be a free model on that provider). All are
read once at startup; an empty key or model leaves the fallback off and the
feature shows the normal offline message.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.common.secrets import secret_matches

log = logging.getLogger("gateway.cloud_fallback")

#: Shown with every answer an online model wrote (API responses and the Bible UI).
ONLINE_NOTE = "Answered by an online model while Jarvis's local AI is offline."

BIBLE_STUDY_ASK = "bible_study_ask"
#: The allowlist when the setting has never been saved.
DEFAULT_FEATURES = BIBLE_STUDY_ASK
SETTING_FEATURES = "cloud_fallback_features"
SETTING_MODEL = "cloud_fallback_model"

READING_LEVELS = ("child", "teen", "adult")
MAX_QUESTION_CHARACTERS = 2_000
MAX_VERSES = 150
MAX_VERSE_CHARACTERS = 2_000
MAX_REFERENCE_CHARACTERS = 80
_TRANSLATION = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class CloudFallbackConfigError(RuntimeError):
    """The allowlist names a feature with no online template (stops startup)."""


class CloudFallbackRefused(RuntimeError):
    """The client would not send this request; ``reason`` is a short code."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


# ------------------------------------------------------------------ payload


@dataclass(frozen=True)
class PublicVerse:
    """One verse of public Scripture text: its reference and its words."""

    reference: str
    text: str


@dataclass(frozen=True)
class BibleStudyRequest:
    """Everything an online model is ever told about a Bible question.

    ``reference`` is the passage asked about (empty for a question of the whole
    Bible, where ``verses`` are what search found). There is deliberately no
    field for a user, notes, highlights, history, library or memory.
    """

    reference: str
    verses: tuple[PublicVerse, ...]
    translation: str
    question: str
    reading_level: str

    def __post_init__(self) -> None:
        if self.reading_level not in READING_LEVELS:
            raise ValueError(f"reading_level must be one of {', '.join(READING_LEVELS)}")
        if not self.question.strip() or len(self.question) > MAX_QUESTION_CHARACTERS:
            raise ValueError("question is empty or too long")
        if not self.verses or len(self.verses) > MAX_VERSES:
            raise ValueError("verses is empty or too long")
        if not all(type(v) is PublicVerse for v in self.verses):
            raise ValueError("verses must be PublicVerse")
        if not _TRANSLATION.match(self.translation):
            raise ValueError("translation is not a translation code")
        if len(self.reference) > MAX_REFERENCE_CHARACTERS:
            raise ValueError("reference is too long")


class _VerseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(..., min_length=1, max_length=MAX_REFERENCE_CHARACTERS)
    text: str = Field(..., min_length=1, max_length=MAX_VERSE_CHARACTERS)


class BibleStudyBody(BaseModel):
    """The route's body. Unknown fields are a 422, never silently dropped, so a
    caller that starts sending more is told at once rather than half-trusted."""

    model_config = ConfigDict(extra="forbid")
    feature: Literal["bible_study_ask"]
    reference: str = Field(default="", max_length=MAX_REFERENCE_CHARACTERS)
    verses: list[_VerseBody] = Field(..., min_length=1, max_length=MAX_VERSES)
    translation: str = Field(..., pattern=_TRANSLATION.pattern)
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    reading_level: Literal["child", "teen", "adult"]

    def to_request(self) -> BibleStudyRequest:
        return BibleStudyRequest(
            reference=self.reference.strip(),
            verses=tuple(PublicVerse(reference=v.reference.strip(), text=v.text.strip()) for v in self.verses),
            translation=self.translation,
            question=self.question.strip(),
            reading_level=self.reading_level,
        )


# ------------------------------------------------------------------ templates

_LEVELS = {
    "child": "The reader is a child of about 6 to 10: short sentences, everyday words, three or four sentences.",
    "teen": "The reader is a teenager: direct and concrete, a few short paragraphs at most.",
    "adult": "The reader is an adult: a full answer in a few paragraphs or a short list, without padding.",
}

_BIBLE_SYSTEM = (
    "You are a careful Bible study helper. Start from the verses given, then bring in what a good "
    "study Bible would: related Scripture, the historical and cultural setting, what the original "
    "words mean and how the church has historically understood it. Hold to traditional, historic "
    "Christian teaching. Quote only the verses given; name any other passage by its reference "
    "(Book Chapter:Verse) without quoting it. Do not invent verse text, references or sources. "
    "Where Christians disagree or something is uncertain, say so and give the main views."
)


def _render_bible_study(req: BibleStudyRequest) -> list[dict[str, str]]:
    """The messages for a Bible question, built from the payload's fields and nothing else."""
    verse_lines = [f"{v.reference} {v.text}" for v in req.verses]
    if req.reference:
        head = [f"Passage: {req.reference} ({req.translation.upper()})", "", *verse_lines]
        ask = f"Answer the question about {req.reference}."
    else:
        head = [f"Verses found by searching the whole Bible ({req.translation.upper()}):", "", *verse_lines]
        ask = ("Answer from these verses and name each one you rely on by its reference. "
               "If they do not answer the question, say so plainly.")
    return [
        {"role": "system", "content": f"{_BIBLE_SYSTEM} {_LEVELS[req.reading_level]}"},
        {"role": "user", "content": "\n".join([*head, "", "Question:", req.question, "", ask])},
    ]


@dataclass(frozen=True)
class _Feature:
    payload_type: type
    render: Callable[[Any], list[dict[str, str]]]


#: Every feature with an online template. Adding one here is the only way a
#: feature can ever be allowlisted; Guardian, chat, Raven and anything holding
#: personal data must never appear.
FEATURES: dict[str, _Feature] = {
    BIBLE_STUDY_ASK: _Feature(BibleStudyRequest, _render_bible_study),
}


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """``cloud_fallback_features``: comma or space separated names; empty turns it off.

    ``None`` (never saved) is the default. An unknown name raises
    ``CloudFallbackConfigError`` naming it, so a typo stops startup instead of
    quietly widening or narrowing what may go online.
    """
    if raw is None:
        raw = DEFAULT_FEATURES
    names = [n.strip().lower() for n in re.split(r"[,\s]+", str(raw)) if n.strip()]
    unknown = sorted({n for n in names if n not in FEATURES})
    if unknown:
        raise CloudFallbackConfigError(
            f"{SETTING_FEATURES} lists {', '.join(repr(u) for u in unknown)}, which has no online template. "
            f"Only these can be listed: {', '.join(sorted(FEATURES))}. Guardian, chat, Raven and anything "
            "with personal data always stay on the local model."
        )
    return frozenset(names)


# ------------------------------------------------------------------ provider


@dataclass(frozen=True)
class CloudProvider:
    url: str
    api_key: str
    model: str
    name: str


def provider_from_settings(settings: dict[str, Any]) -> CloudProvider | None:
    """The existing online provider settings plus ``cloud_fallback_model``; None if any is missing."""
    from services.gateway.llm_providers import CLOUD_PROVIDER_DEFAULT_URLS

    url = str(settings.get("llm_cloud_url") or "").strip()
    key = str(settings.get("llm_cloud_api_key") or "").strip()
    model = str(settings.get(SETTING_MODEL) or "").strip()
    if not (url and key and model):
        return None
    name = next((n for n, u in CLOUD_PROVIDER_DEFAULT_URLS.items() if u == url), "custom")
    if name == "openrouter" and not model.endswith(":free"):
        log.warning("[cloud-fallback] %s=%r is not an OpenRouter ':free' model; it may be billed", SETTING_MODEL, model)
    return CloudProvider(url=url, api_key=key, model=model, name=name)


# ------------------------------------------------------------------ client


async def _send(provider: CloudProvider, messages: list[dict[str, str]]) -> str:
    """The one place a request leaves the house: the existing OpenAI-compatible adapter."""
    from services.gateway.llm_providers import OpenAICompatibleProvider

    adapter = OpenAICompatibleProvider(api_key=provider.api_key, base_url=provider.url,
                                       provider_name=provider.name, timeout=120.0)
    return await adapter.generate(provider.model, messages, options={"temperature": 0.3})


class CloudFallbackClient:
    """Sends an allowlisted feature's typed payload to the online provider, or refuses."""

    def __init__(self, allowlist: frozenset[str], provider: CloudProvider | None,
                 local_down: Callable[[], bool]):
        self.allowlist = allowlist
        self.provider = provider
        self._local_down = local_down
        self._logged: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def _refuse(self, feature: str, reason: str, message: str) -> CloudFallbackRefused:
        with self._lock:
            first = (feature, reason) not in self._logged
            self._logged.add((feature, reason))
        if first:
            log.warning("[cloud-fallback] refused feature=%s reason=%s", feature, reason)
        return CloudFallbackRefused(reason, message)

    async def answer(self, feature: str, payload: object) -> str:
        spec = FEATURES.get(feature)
        if spec is None or feature not in self.allowlist:
            raise self._refuse(str(feature)[:40], "not_allowed", "This feature may not use an online model.")
        if type(payload) is not spec.payload_type:
            raise self._refuse(feature, "payload_type", "Only the feature's own minimal payload may go online.")
        if not self._local_down():
            raise self._refuse(feature, "local_up", "The local AI is up, so nothing goes online.")
        if self.provider is None:
            raise self._refuse(feature, "no_provider", "No online provider and model are configured.")
        text = (await _send(self.provider, spec.render(payload))).strip()
        if not text:
            raise CloudFallbackRefused("empty", "The online model returned an empty answer.")
        log.info("[cloud-fallback] answered online: feature=%s", feature)
        return text


#: Off until ``configure`` runs at startup: nothing is allowlisted, no provider.
CLIENT = CloudFallbackClient(frozenset(), None, lambda: False)


def configure(settings: dict[str, Any] | None) -> CloudFallbackClient:
    """Read the allowlist and provider once (gateway startup). Raises on an unknown feature."""
    from services.gateway import local_ai

    global CLIENT
    settings = settings or {}
    raw = settings.get(SETTING_FEATURES)
    allowlist = parse_allowlist(None if raw is None else str(raw))
    provider = provider_from_settings(settings) if allowlist else None
    CLIENT = CloudFallbackClient(allowlist, provider, local_ai.LLM.is_down)
    log.info("[cloud-fallback] features=%s provider=%s", ",".join(sorted(allowlist)) or "none",
             f"{provider.name}:{provider.model}" if provider else "none")
    return CLIENT


# ------------------------------------------------------------------ route

router = APIRouter()


@router.post("/internal/cloud-fallback/bible-study")
async def bible_study_online(request: Request) -> JSONResponse:
    """A Bible question for the online model while the local AI is offline (services only).

    200 ``{answer, note, online: true}``; 503 with a ``reason`` when it is not
    allowed, no provider is set or the provider failed (the caller then shows
    its normal offline sentence); 409 when the local AI is up; 422 for any body
    but the minimal payload.
    """
    from services.gateway.config import INTERNAL_SECRET

    if not secret_matches(request.headers.get("X-Internal-Secret") or "", INTERNAL_SECRET):
        return JSONResponse({"detail": "internal only"}, status_code=403)
    try:
        body = BibleStudyBody.model_validate(await request.json())
        payload = body.to_request()
    except (ValidationError, ValueError) as exc:
        log.warning("[cloud-fallback] refused feature=%s reason=payload_shape (%s)", BIBLE_STUDY_ASK, type(exc).__name__)
        return JSONResponse({"detail": "Only the minimal Bible question payload is accepted.",
                             "reason": "payload_shape"}, status_code=422)
    try:
        answer = await CLIENT.answer(BIBLE_STUDY_ASK, payload)
    except CloudFallbackRefused as exc:
        if exc.reason == "local_up":
            return JSONResponse({"detail": str(exc), "reason": exc.reason}, status_code=409)
        return JSONResponse({"detail": str(exc), "reason": exc.reason}, status_code=503)
    except Exception as exc:  # the provider failed: the caller keeps its offline sentence
        log.warning("[cloud-fallback] online model failed for feature=%s: %s", BIBLE_STUDY_ASK, type(exc).__name__)
        return JSONResponse({"detail": "The online model did not answer.", "reason": "provider_failed"}, status_code=503)
    return JSONResponse({"answer": answer, "note": ONLINE_NOTE, "online": True, "feature": BIBLE_STUDY_ASK})


__all__ = [
    "BIBLE_STUDY_ASK",
    "CLIENT",
    "FEATURES",
    "ONLINE_NOTE",
    "BibleStudyBody",
    "BibleStudyRequest",
    "CloudFallbackClient",
    "CloudFallbackConfigError",
    "CloudFallbackRefused",
    "CloudProvider",
    "PublicVerse",
    "configure",
    "parse_allowlist",
    "provider_from_settings",
    "router",
]
