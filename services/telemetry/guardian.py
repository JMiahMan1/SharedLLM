"""Watching (Phase 11, step 6): content monitoring and the parents' weekly recap.

Every activity event recorded for a child is scanned as it lands
(``services/common/concern_monitor.py``); a concern is kept for the recap, and
one that the child's sensitivity says pages a parent is sent to both parents
at once (deduplicated per child, category and day). On Sunday evening in the
household's time zone each child's recap (``services/common/guardian_recap.py``)
is built from the week's data, kept, and summarised to both parents.

Only minors with parents are watched; an adult's events are never scanned.

Second pass: each phrase-list hit is read in context by the household's own
model (``llm_local_url`` with the ``guardian_monitor_model`` setting) before
anyone is paged (``services/common/concern_review.py``). Without a model, or
when it cannot be reached, the phrase list decides alone and the reason is
logged and kept on the hit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

from services.common import concern_monitor, concern_review, device_noun, guardian_recap, notice_links, video_calls
from services.common.backend_health import BackendUnavailable
from services.telemetry import app_updates, llm, notices, store
from services.telemetry.config import EXECUTION_SVC, INTERNAL_SECRET

log = logging.getLogger("telemetry.guardian")

CHILDREN_CACHE_SECONDS = 300
RECAP_HOUR = 18  # Sunday, household time
RECAP_TICK_SECONDS = 900
BLOCKED_SITE_ALERT_THRESHOLD = 5  # blocked attempts on one domain in a day before paging parents

_children_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
SETTINGS_CACHE_SECONDS = 60
MODEL_TIMEOUT_SECONDS = 25
# A photo check can wait for the vision model to be loaded back onto the card
# (about 25 s after a chat model displaced it) and then answer (about 10 s).
MEDIA_MODEL_TIMEOUT_SECONDS = 60


class MediaScanUnavailable(Exception):
    """The vision check could not run (settings unreadable, the model failed or
    timed out). Not the same as a clean picture: the phone must try it again."""
_settings_cache: tuple[float, dict[str, str]] | None = None

#: A voice note can be a minute of speech on the CPU: Whisper is given time to
#: finish (it is the reason these are scanned rather than dropped).
AUDIO_TIMEOUT_SECONDS = 180
#: The most audio one request may carry, decoded. A voice note is far smaller;
#: past this it is not a voice note.
MAX_AUDIO_BYTES = 8 * 1024 * 1024

#: What Whisper answers when a clip holds no speech. Recording an activity for
#: it would tell a parent nothing and cost a phrase scan for nothing.
NO_SPEECH = frozenset({"", "[silence]", "(silence)", "[blank_audio]", "[ music ]", "[music]", "..."})


class AudioScanUnavailable(Exception):
    """The voice note could not be transcribed (Whisper is off, the service is
    unreachable or busy). The phone keeps the clip and retries; nothing is
    dropped, and the parent is told nothing rather than told it was clean."""


#: media_type and concern feature of a screenshot taken during a video call.
VIDEO_CALL = "video_call"
#: How long one call's confirmed-frame tally and "parents told" mark are kept.
VIDEO_CALL_STATE_SECONDS = 12 * 3600
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")

Deliver = Callable[[dict[str, Any]], Awaitable[Any]]


def _iso_at(value: Any) -> str:
    """An event's timestamp as ISO text: strings pass through, epoch numbers convert."""
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return store.utcnow_iso()


def household_now() -> datetime:
    """Now in the household zone (services/common/household_tz.py); raises with how to set it."""
    from services.common import household_tz

    return datetime.now(household_tz.zoneinfo())


async def children(session) -> dict[str, dict[str, Any]]:
    """Minors with parents, by username (identity owns the rule)."""
    global _children_cache
    if _children_cache and _children_cache[0] > time.monotonic():
        return _children_cache[1]
    data = await notices._identity_get(session, "/api/internal/guardian/children")
    by_name = {c["username"]: c for c in (data or {}).get("children") or [] if c.get("username")}
    _children_cache = (time.monotonic() + CHILDREN_CACHE_SECONDS, by_name)
    return by_name


def _device(child: dict[str, Any]) -> str:
    """What the child carries, from identity's ``device_kinds`` ("tablet")."""
    return device_noun.noun(child.get("device_kinds"))


def _concern_notice(parent: str, child: dict[str, Any], concern: dict[str, Any], day: str) -> dict[str, Any]:
    name = child.get("display_name") or child["username"]
    priority = 5 if concern["category"] in concern_monitor.ALWAYS_ON else 4 if concern["level"] == "severe" else 3
    review = concern.get("review") or {}
    body = concern.get("excerpt") or ""
    # A parent deciding what to do needs to know who sent it before they need
    # the description: "From Alex (MMS)" is the difference between a bully and
    # a friend quoting a song.
    from_message = concern.get("message_context")
    if isinstance(from_message, dict):
        summary = concern_review.message_summary(from_message, with_text=False)
        said = from_message.get("message_text")
        if summary:
            body = f"{summary}.\n{body}" if body else f"{summary}."
        if said:
            body = f'{body}\nMessage: "{said}"' if body else f'Message: "{said}"'
        body = body[:400]
    if review.get("by") == "model" and review.get("reason"):
        body = f"{body}\n{'Checked' if review.get('verdict') == 'confirmed' else 'Unsure'}: {review['reason']}"
    return {
        "user": parent,
        "category": "family_safety",
        "kind": "concern",
        "priority": priority,
        "title": f"{concern['label']} on {name}'s {_device(child)}",
        "body": body,
        "tag": f"concern:{child['username']}:{concern['category']}",
        "once": f"concern:{child['username']}:{concern['category']}:{day}",
        # It quotes what was flagged (a message, often): never to a minor,
        # whoever is on the parents list.
        "audience": "parents",
        # The child's page, at the concern (its review, its export, its clear).
        "data": {"link": notice_links.link("member", username=child["username"],
                                           focus=f"concern-{concern['id']}" if concern.get("id") else None),
                 "child": child["username"], "level": concern["level"]},
    }


def _blocked_site_notice(parent: str, child: dict[str, Any], domain: str, count: int, day: str) -> dict[str, Any]:
    name = child.get("display_name") or child["username"]
    return {
        "user": parent,
        "category": "family_safety",
        "kind": "blocked_site",
        "priority": 3,
        "title": f"Blocked site on {name}'s {_device(child)}",
        "body": f"{name}'s {_device(child)} tried to open {domain} ({count} blocked attempts today).",
        "tag": f"blocked_site:{child['username']}:{domain}",
        "once": f"blocked_site:{child['username']}:{domain}:{day}",
        "data": {"link": notice_links.link("member", username=child["username"], section="apps"),
                 "child": child["username"], "domain": domain, "count": count},
    }


async def _check_blocked_site(rc, child: dict[str, Any], event: dict[str, Any], deliver: Deliver, day: str) -> None:
    """Page both parents once a day when a child repeatedly hits the same blocked domain."""
    if str(event.get("feature") or "") != "web" or child.get("monitoring") == "off":
        return
    domain = str(event.get("item") or "").strip().lower()
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    try:
        blocked = int(detail.get("blocked") or (1 if event.get("action") == "blocked" else 0))
    except (TypeError, ValueError):
        blocked = 0
    if not domain or blocked <= 0:
        return
    count_key = f"tel:blocked_site_count:{child['username']}:{domain}:{day}"
    total = int(await rc.get(count_key) or 0) + blocked
    await rc.set(count_key, str(total), ex=2 * 86400)
    if total < BLOCKED_SITE_ALERT_THRESHOLD:
        return
    if not await rc.set(f"tel:blocked_site_sent:{child['username']}:{domain}:{day}", "1", nx=True, ex=2 * 86400):
        return
    for parent in child.get("parents") or []:
        try:
            await deliver(_blocked_site_notice(parent, child, domain, total, day))
        except Exception as exc:
            log.warning("blocked-site notice to %s failed: %s", parent, exc)


async def _model_settings(session) -> dict[str, str]:
    """llm_local_url, guardian_monitor_model and vision settings from identity (cached a minute)."""
    global _settings_cache
    if _settings_cache and _settings_cache[0] > time.monotonic():
        return _settings_cache[1]
    found = await notices.identity_settings(
        session, ("llm_local_url", "guardian_monitor_model", "vision_ocr_proxy_url", "vision_ocr_model")
    )
    _settings_cache = (time.monotonic() + SETTINGS_CACHE_SECONDS, found)
    return found


async def scan_media_item(
    session,
    child: dict[str, Any],
    media_type: str,
    file_name: str,
    image_b64: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Scan one image or thumbnail from a child's device using local vision model.

    Returns the concern dict if a concern is detected, else None.
    The image is never displayed or written to disk; its encoded payload is
    held encrypted for a parent/admin export (see store.save_concern_image).
    """
    if child.get("monitoring") == "off":
        return None
    # A video call frame has its own switch (video_calls.scan, checked by the
    # caller); everything else follows photo scanning.
    if media_type != VIDEO_CALL and child.get("photo_scan") is False:
        return None
    try:
        settings = await _model_settings(session)
    except Exception as exc:
        log.warning("media scan: model settings could not be read: %s", exc)
        raise MediaScanUnavailable(f"model settings could not be read: {exc}") from exc

    url = settings.get("vision_ocr_proxy_url") or settings.get("llm_local_url") or ""
    model = settings.get("vision_ocr_model") or settings.get("guardian_monitor_model") or "qwen2.5-vl:7b"
    if not url:
        log.info("media scan: no vision endpoint configured")
        return None

    payload = {
        "model": model,
        "messages": concern_review.media_messages(media_type, file_name, image_b64, metadata),
        "temperature": 0,
        "max_tokens": 200,
        "stream": False,
    }
    try:
        content = await llm.local_chat_content(
            session, f"{url.rstrip('/')}/v1/chat/completions", payload, MEDIA_MODEL_TIMEOUT_SECONDS, background=True
        )
    except BackendUnavailable as exc:
        # The GPU box is off (logged once by the breaker). Not checked is not clean:
        # the caller answers 503 with Retry-After and the phone keeps the picture.
        raise MediaScanUnavailable("the local vision model is offline") from exc
    except Exception as exc:
        log.warning("media scan: the vision model did not answer for %s (%s): %r", child.get("username"), file_name, exc)
        raise MediaScanUnavailable(f"the vision model did not answer: {exc!r}") from exc
    try:
        verdict = concern_review.parse_media_verdict(content)
        if not verdict["concern"]:
            return None
        cat = verdict["category"]
        spec = concern_monitor.CATEGORIES.get(cat, concern_monitor.CATEGORIES["sexual"])
        description = verdict["description"] or f"Safety concern in {media_type} '{file_name}'"
        context_parts = [description]
        if verdict["reason"]:
            context_parts.append(f"Checked: {verdict['reason']}")
        # Who sent it, in which app, and the words that came with it: a parent
        # needs that more than the file name when deciding what to say.
        from_message = concern_review.message_context(metadata)
        if from_message:
            summary = concern_review.message_summary(from_message, with_text=len(context_parts) < 3)
            if summary:
                context_parts.append(summary)
        if isinstance(metadata, dict):
            for key in ("app", "package", "folder", "album", "title", "taken_at"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    context_parts.append(f"{key.replace('_', ' ')}: {value.strip()[:80]}")
        concern = {
            "id": store.new_id(),
            "category": cat,
            "label": spec["label"],
            "level": verdict["level"],
            "excerpt": description,
            "context": " · ".join(context_parts)[:500],
            "item": file_name,
            "feature": "media",
            "action": f"{media_type}_scan",
            "file_name": file_name,
            "at": household_now().isoformat(),
            "message_context": from_message,
            "review": {
                "by": "vision",
                "model": model,
                "verdict": "confirmed",
                "reason": verdict["reason"],
            },
        }
        return concern
    except Exception as exc:
        log.warning("media scan error for %s (%s): %s", child.get("username"), file_name, exc)
        return None


_AUDIO_SUFFIX = {
    "audio/amr": ".amr",
    "audio/3gpp": ".3gp",
    "audio/3gpp2": ".3g2",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
}

#: Whisper's smallest model transcribes a phone voice note well and runs on the
#: CPU without taking the card from the household.
AUDIO_WHISPER_MODEL = "base"


def audio_file_name(mime_type: str, file_name: str = "") -> str:
    """A name the transcriber can pick a decoder from.

    Whisper's loader believes an extension over a content type, so a clip named
    without one - which is what a notification URI or an MMS part gives us - is
    given the extension its type implies. Android's own recordings are m4a, so
    that is the fallback rather than .wav (a container that is not wav is not
    decodable when the loader trusts the name).
    """
    name = (file_name or "").strip().replace("/", "_").replace("\\", "_")[:120]
    leaf = name.rsplit("/", 1)[-1]
    if "." in leaf and len(leaf.rsplit(".", 1)[-1]) <= 5:
        return name
    suffix = _AUDIO_SUFFIX.get((mime_type or "").split(";")[0].strip().lower(), ".m4a")
    return f"{name or 'voice-note'}{suffix}"


async def transcribe_audio(session, audio: bytes, mime_type: str, file_name: str) -> str:
    """The words in one clip, from the household's own Whisper.

    The execution service already runs Whisper and ffmpeg on this LAN, so this
    asks it rather than adding a second transcriber to telemetry. Nothing is
    written here: the bytes go into the multipart form, and execution deletes
    its own temporary file when it is done. A 501 (no Whisper in that image) is
    an unavailability, not a verdict: the phone keeps the clip.
    """
    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio,
        filename=audio_file_name(mime_type, file_name),
        content_type=(mime_type or "audio/mp4"),
    )
    form.add_field("model", AUDIO_WHISPER_MODEL)
    form.add_field("language", "en")
    url = f"{EXECUTION_SVC.rstrip('/')}/execute/stt/transcribe"
    try:
        async with session.post(
            url,
            data=form,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=AUDIO_TIMEOUT_SECONDS),
        ) as response:
            status = response.status
            body = await response.text()
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        raise AudioScanUnavailable(f"the transcriber could not be reached: {exc!r}") from exc
    if status >= 500 or status == 501:
        # 501 is "Whisper not installed" from the execution image, and 503/504
        # are the GPU box busy or restarting: both mean try again later.
        raise AudioScanUnavailable(f"the transcriber is not available (HTTP {status})")
    if status >= 400:
        raise AudioScanUnavailable(f"the transcriber refused the clip (HTTP {status})")
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise AudioScanUnavailable("the transcriber's answer could not be read") from exc
    transcript = data.get("transcript") if isinstance(data, dict) else None
    if not isinstance(transcript, str):
        raise AudioScanUnavailable("the transcriber answered without a transcript")
    return transcript.strip()


def voice_message_event(
    user: str,
    transcript: str,
    mime_type: str,
    file_name: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """The child's ``sms`` activity for one transcribed voice note.

    It is recorded as a text message the child handled, with the transcript in
    its body, so the phrase list, the local model, the recap and the Calls &
    messages view all read it the way they read a written message - and the
    view can badge it as spoken because ``detail.audio`` is true.
    """
    context = concern_review.message_context(metadata) or {}
    at = context.get("sent_at") or (metadata or {}).get("at") if isinstance(metadata, dict) else None
    if not isinstance(at, (int, float)):
        at = int(time.time() * 1000)
    # The feed and the Calls & messages view read a top-level ISO stamp (a
    # number there is skipped as unreadable); the phone's own convention for
    # the detail is milliseconds, so both are kept.
    when = _iso_at(at)
    item = context.get("contact") or context.get("number") or (file_name or "a voice message")
    detail = {
        "body": transcript,
        "transcript": transcript,
        "audio": True,
        "mime_type": mime_type,
        "file_name": file_name,
        "direction": context.get("direction"),
        "number": context.get("number"),
        "contact": context.get("contact"),
        "group": context.get("group"),
        "mms": bool(context.get("source") == "mms"),
        "source": context.get("source") or "audio",
        "message_text": context.get("message_text"),
        "at": at,
    }
    return {
        "user": user,
        "feature": "sms",
        "event": "message",
        "action": "voice_message",
        "item": item,
        "detail": detail,
        "at": when,
        "message_context": context or None,
    }


async def verify_media_frame(session, category: str, image_b64: str) -> dict[str, Any]:
    """The second, stricter look at a flagged frame (concern_review.media_verify_messages).

    Returns {"confirmed": bool, "reason": str, "model": str}. Anything that
    keeps it from answering (no endpoint, an error, a reply that is not the
    expected JSON) is ``confirmed: False`` with the reason: a frame that was
    not confirmed never pages anyone.
    """
    try:
        settings = await _model_settings(session)
    except Exception as exc:
        return {"confirmed": False, "reason": f"second check unavailable: model settings could not be read ({exc})", "model": ""}
    url = settings.get("vision_ocr_proxy_url") or settings.get("llm_local_url") or ""
    model = settings.get("vision_ocr_model") or settings.get("guardian_monitor_model") or "qwen2.5-vl:7b"
    if not url:
        return {"confirmed": False, "reason": "second check unavailable: no vision endpoint configured", "model": model}
    payload = {
        "model": model,
        "messages": concern_review.media_verify_messages(category, image_b64),
        "temperature": 0,
        "max_tokens": 120,
        "stream": False,
    }
    try:
        content = await llm.local_chat_content(
            session, f"{url.rstrip('/')}/v1/chat/completions", payload, MODEL_TIMEOUT_SECONDS
        )
        check = concern_review.parse_media_check(content)
    except Exception as exc:
        if not isinstance(exc, BackendUnavailable):
            log.warning("video call second check failed: %s", exc)
        return {"confirmed": False, "reason": f"second check unavailable: {exc or type(exc).__name__}", "model": model}
    return {**check, "model": model}


def _video_call_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    """The call a frame came from, cleaned: app, package, site, when it
    started, the frame's number and the phone's id for the call."""
    m = metadata if isinstance(metadata, dict) else {}
    site = video_calls.normalize_site(str(m.get("site") or ""))
    package = str(m.get("package") or "").strip()
    started = str(m.get("call_started_at") or "").strip()[:40]
    try:
        frame = max(0, int(m.get("frame_index") or 0))
    except (TypeError, ValueError):
        frame = 0
    call_id = str(m.get("call_id") or "").strip()
    if not _CALL_ID.fullmatch(call_id):
        # A phone that sent no id: the app or site and the start time name the call.
        call_id = re.sub(r"[^A-Za-z0-9_.:-]", "", f"{package or site or 'call'}:{started}")[:80] or "call"
    return {
        "app": str(m.get("app") or "").strip()[:60],
        "package": package if video_calls.PACKAGE.match(package) else "",
        "site": site if video_calls.DOMAIN.match(site) else "",
        "call_started_at": started,
        "frame_index": frame,
        "call_id": call_id,
        "label": video_calls.call_label(m),
    }


def _video_call_notice(parent: str, child: dict[str, Any], concern: dict[str, Any], day: str) -> dict[str, Any]:
    """The photo concern notice, reworded for a call and sent once per call and category."""
    notice = _concern_notice(parent, child, concern, day)
    call = concern.get("video_call") or {}
    name = child.get("display_name") or child["username"]
    alert = concern.get("alert") or {}
    notice["title"] = f"{concern['label']} on a video call on {name}'s {_device(child)}"
    notice["body"] = (
        f"Video call · {call.get('label') or 'a video call'}. {concern.get('excerpt') or ''} "
        f"Seen on {alert.get('confirmed_frames') or 0} frames, each checked twice. Review it in Jarvis."
    ).strip()
    notice["tag"] = f"concern:{child['username']}:video_call:{call.get('call_id')}"
    notice["once"] = f"concern:{child['username']}:video_call:{call.get('call_id')}:{concern['category']}"
    notice["data"] = {**notice["data"], "feature": VIDEO_CALL, "concern_id": concern.get("id")}
    return notice


async def scan_video_call_frame(
    rc,
    session,
    child: dict[str, Any],
    file_name: str,
    image_b64: str,
    metadata: dict[str, Any] | None,
    deliver: Deliver,
) -> dict[str, Any] | None:
    """One screenshot from a child's video call: scanned like a photo, but a
    parent is only notified when the flag is corroborated.

    The owner's rule: a false positive must never trigger anything. So:

    1. the first vision look flags the frame (``scan_media_item``), or nothing happens;
    2. a second, stricter look at the same frame (``verify_media_frame``) must agree;
    3. at least ``corroborate_frames`` (default 2) frames of the same category in
       the same call must each pass both looks;
    4. then each parent is notified once for that call and category (at their
       chosen level), and never again for later frames of it.

    Anything short of that is kept in the review queue, silently, marked
    "Unsure, not sent". Nothing here blocks, locks or changes a setting: a
    flag only ever informs.
    """
    cfg = child.get("video_calls") if isinstance(child.get("video_calls"), dict) else {}
    if child.get("monitoring") == "off" or cfg.get("scan") is False:
        return None
    if cfg.get("scan") is None and child.get("photo_scan") is False:
        return None  # an identity too old to say: follow photo scanning
    try:
        needed = max(video_calls.CORROBORATE_MIN, int(cfg.get("corroborate_frames") or video_calls.CORROBORATE_DEFAULT))
    except (TypeError, ValueError):
        needed = video_calls.CORROBORATE_DEFAULT
    call = _video_call_meta(metadata or {})
    try:
        concern = await scan_media_item(session, child, VIDEO_CALL, file_name, image_b64, call)
    except MediaScanUnavailable:
        return None  # a live call sends the next frame anyway; nothing to retry
    if not concern:
        return None
    category = concern["category"]
    check = await verify_media_frame(session, category, image_b64)
    user = child["username"]
    confirmed_frames = 0
    if check["confirmed"]:
        tally = f"tel:video_call:frames:{user}:{call['call_id']}:{category}"
        await rc.sadd(tally, str(call["frame_index"]))
        await rc.expire(tally, VIDEO_CALL_STATE_SECONDS)
        confirmed_frames = len(await rc.smembers(tally))
    corroborated = check["confirmed"] and confirmed_frames >= needed
    concern.update({
        "feature": VIDEO_CALL,
        "action": "video_call_frame",
        "item": call["label"],
        "video_call": call,
        "context": " · ".join(
            p for p in (
                f"Video call · {call['label']}",
                f"started {call['call_started_at']}" if call["call_started_at"] else "",
                f"frame {call['frame_index'] + 1}",
                concern.get("excerpt") or "",
            ) if p
        )[:500],
    })
    review = dict(concern.get("review") or {})
    review["second_check"] = {"confirmed": check["confirmed"], "reason": check.get("reason") or "", "model": check.get("model") or ""}
    if not corroborated:
        # Kept for a parent to look at, but nobody is told.
        review["verdict"] = "unsure"
    concern["review"] = review
    state = "unsure"
    if corroborated:
        state = "already_sent"
        sent_key = f"tel:video_call:told:{user}:{call['call_id']}:{category}"
        if await rc.set(sent_key, "1", nx=True, ex=VIDEO_CALL_STATE_SECONDS):
            state = "sent"
    concern["alert"] = {"state": state, "confirmed_frames": confirmed_frames, "needed": needed}
    if await store.save_concern_image(rc, user, str(concern.get("id") or ""), image_b64):
        concern["has_image"] = True
        concern["image_kept_days"] = store.CONCERN_IMAGE_TTL_SECONDS // 86400
    await store.add_concerns(rc, user, [concern])
    if state == "sent":
        day = household_now().date().isoformat()
        for parent in child.get("parents") or []:
            # Each parent chose how serious a concern must be to hear now.
            if not concern_monitor.pages_parent(concern, concern_monitor.parent_level(child, parent)):
                continue
            try:
                await deliver(_video_call_notice(parent, child, concern, day))
            except Exception as exc:  # one parent's failure never blocks the other
                log.warning("video call notice to %s failed: %s", parent, exc)
    return concern


async def _ask_model(session, url: str, model: str, concern: dict[str, Any], text: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": concern_review.messages(concern, text),
        "temperature": 0,
        "max_tokens": 200,
        "stream": False,
    }
    content = await llm.local_chat_content(session, f"{url.rstrip('/')}/v1/chat/completions", payload, MODEL_TIMEOUT_SECONDS,
                                           background=True)
    return concern_review.parse_verdict(content)


async def second_pass(session, event: dict[str, Any], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each candidate after the local model's look, or as the phrase list found it
    (with the reason) when the model cannot weigh in. Never raises, never drops."""
    try:
        settings = await _model_settings(session)
    except Exception as exc:
        reason = f"model settings could not be read: {exc}"
        log.warning("content monitoring: %s; using the phrase list", reason)
        return [concern_review.phrase_only(c, reason) for c in found]
    url, model = settings.get("llm_local_url") or "", settings.get("guardian_monitor_model") or ""
    if not url or not model:
        reason = "no local model configured (llm_local_url and guardian_monitor_model)"
        log.info("content monitoring: %s; using the phrase list", reason)
        return [concern_review.phrase_only(c, reason) for c in found]
    text = concern_monitor.event_text(event)
    out = []
    for concern in found:
        try:
            verdict = await _ask_model(session, url, model, concern, text)
            out.append(concern_review.apply(concern, verdict, model))
        except Exception as exc:
            if isinstance(exc, BackendUnavailable):
                # Logged once by the breaker; the phrase list decides meanwhile, as designed.
                reason = "local model offline"
            else:
                reason = f"local model unavailable: {exc or type(exc).__name__}"
                log.warning("content monitoring: %s; using the phrase list for %s", reason, concern.get("category"))
            out.append(concern_review.phrase_only(concern, reason))
    return out


async def on_activity(rc, session, event: dict[str, Any], deliver: Deliver) -> list[dict[str, Any]]:
    """Scan one recorded event; keep and page what it finds. Never raises."""
    try:
        child = (await children(session)).get(str(event.get("user") or ""))
        if not child:
            return []
        day = household_now().date().isoformat()
        await _check_blocked_site(rc, child, event, deliver, day)
        found = concern_monitor.scan_event(event)
        if not found:
            return []
        found = await second_pass(session, event, found)
        for concern in found:
            concern.setdefault("id", store.new_id())
            concern["at"] = _iso_at(concern.get("at"))
            # A voice note's concerns keep who said it, so a parent's notice
            # names the person instead of only quoting the words.
            if event.get("message_context") and not concern.get("message_context"):
                concern["message_context"] = event["message_context"]
        await store.add_concerns(rc, child["username"], found)
        for concern in found:
            for parent in child.get("parents") or []:
                # Each parent chose how serious a concern must be to hear now.
                if not concern_monitor.pages_parent(concern, concern_monitor.parent_level(child, parent)):
                    continue
                try:
                    await deliver(_concern_notice(parent, child, concern, day))
                except Exception as exc:  # one parent's failure never blocks the other
                    log.warning("concern notice to %s failed: %s", parent, exc)
        return found
    except Exception as exc:
        log.warning("content monitoring skipped an event: %s", exc)
        return []


async def record_activity(rc, session, event: dict[str, Any], deliver: Deliver) -> list[dict[str, Any]]:
    """Keep one activity event on its person's feed and scan it (a child's) for
    concerns: the one path for every recorder (the activity route, Spotify)."""
    await store.append_activity(rc, str(event.get("user") or ""), event)
    return await on_activity(rc, session, event, deliver)


async def build_recap(rc, username: str, day: date) -> dict[str, Any]:
    start, end = guardian_recap.week_bounds(day)
    usage = await store.get_usage(rc, username, 21)
    events = await store.list_activity(rc, username, store.ACTIVITY_MAX)
    concerns = await store.list_concerns(rc, username)
    return guardian_recap.build(usage, events, concerns, start, end, household_now().tzinfo.key)


async def send_weekly_recaps(rc, session, deliver: Deliver, now: datetime) -> int:
    """Sunday from 18:00: build, keep and send each child's recap once."""
    if now.weekday() != 6 or now.hour < RECAP_HOUR:
        return 0
    sent = 0
    week = guardian_recap.week_bounds(now.date())[0].isoformat()
    for child in (await children(session)).values():
        sent_key = f"tel:recap_sent:{child['username']}:{week}"
        if not await rc.set(sent_key, "1", nx=True, ex=8 * 86400):
            continue
        try:
            recap = await build_recap(rc, child["username"], now.date())
            await store.save_recap(rc, child["username"], week, recap)
        except Exception as exc:
            # Not sent: let the next sweep this evening try again.
            await rc.delete(sent_key)
            log.warning("recap for %s failed: %s", child["username"], exc)
            continue
        line = guardian_recap.summary_line(child.get("display_name") or child["username"], recap, _device(child))
        for parent in child.get("parents") or []:
            try:
                await deliver({
                    "user": parent,
                    "category": "family_safety",
                    "kind": "recap",
                    "title": f"{child.get('display_name') or child['username']}'s week",
                    "body": line,
                    "tag": f"recap:{child['username']}:{week}",
                    # Opens this recap itself, not the member page.
                    "data": {"link": notice_links.link("guardian", member=child["username"], area="overview", week=week,
                                                       focus=f"recap-{week}"),
                             "child": child["username"], "week": week},
                })
            except Exception as exc:
                log.warning("recap notice to %s failed: %s", parent, exc)
        sent += 1
    return sent


async def recap_loop(redis_factory, session_factory, deliver: Deliver) -> None:
    rc = redis_factory()
    while True:
        try:
            await send_weekly_recaps(rc, session_factory(), deliver, household_now())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("weekly recap tick failed: %s", exc)
        try:
            # The same tick tells people whose phones missed an app update.
            session = session_factory()
            await app_updates.remind_missed_updates(rc, session, deliver, await children(session))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("missed-update reminder tick failed: %s", exc)
        await asyncio.sleep(RECAP_TICK_SECONDS)
