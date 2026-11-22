"""The GPU box's services as the gateway sees them: up or down, and what to tell people.

The local AI (the alpaca proxy, ``llm_local_url``), the arcade
(``ALPACA_ARCADE_URL``), the audio server (``ALPACA_AUDIO_URL``) and the image
server all run on one machine on the LAN. Each has a ``BackendHealth``
(services/common/backend_health.py) here, shared by the API loop and the
Raven worker loop of this one process: the first call that cannot connect
marks it down, every later call fails at once with a plain sentence, and a
background probe brings it back.

What people are told lives here too, one sentence per feature, with no host
names, addresses or exception text. The detail is in the log under the error
id the response carries.

There is no fallback to an online model here on purpose: anything sensitive may
only ever run on the local model, so while it is offline the answer is "not now",
never "somewhere else". The one exception is cloud_fallback.py: an allowlist of
features with no personal data (a Bible question), each sending only its own
minimal typed payload, and only while this breaker is open.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi.responses import JSONResponse

from services.common.backend_health import (
    BackendHealth,
    BackendUnavailable,
    Settings,
    is_unreachable,
)

log = logging.getLogger("gateway.local_ai")

SETTINGS = Settings.from_env()

#: Said in chat (typed) when a question needs the local model and it is offline.
CHAT_OFFLINE = (
    "Jarvis's thinking brain (the local AI) is offline right now, so I can still control the house "
    "and tell you the time and the weather, but I can't answer that yet. "
    "I'll let you know when it's back."
)
#: Said aloud by a voice satellite: shorter, no parentheses.
VOICE_OFFLINE = (
    "My thinking brain is offline right now. I can still control the house and tell you the time "
    "and the weather, but I can't answer that yet."
)
#: A Raven mission or other background job that is waiting rather than failing.
MISSION_WAITING = (
    "Waiting for the local AI to come back. This mission is paused and will pick up again on its own."
)
#: A background job (trainer, summaries, studio) that is paused.
BACKGROUND_PAUSED = "Paused until the local AI is back. Nothing was lost; it will carry on by itself."
ARCADE_OFFLINE = "The arcade is offline right now because the game computer is off. The games will be back when it is."
IMAGES_OFFLINE = "Picture making is offline right now because the computer that draws them is off. Please try again later."
AUDIO_OFFLINE = "Music and voice making are offline right now because that computer is off. Please try again later."
#: A model call that failed for another reason (the model server was busy, or
#: answered with an error): the detail is logged under the error id.
MODEL_TROUBLE = "Jarvis couldn't get an answer from the local AI just now. Please try again in a minute."
#: Anything else that went wrong while answering.
ANSWER_FAILED = "Something went wrong while Jarvis was answering. Please try again."
#: The notice sent to people who were told "I'll let you know".
BACK_TITLE = "Jarvis's thinking brain is back"
BACK_BODY = "The local AI is working again, so you can ask your question now."

LLM = BackendHealth(
    "local_ai", "Jarvis's thinking brain", probe_path="/", message=CHAT_OFFLINE,
    settings=SETTINGS, logger=log,
)
ARCADE = BackendHealth(
    "arcade", "The arcade", probe_path="/", message=ARCADE_OFFLINE, settings=SETTINGS, logger=log,
)
AUDIO = BackendHealth(
    "audio", "Music and voice making", probe_path="/health", message=AUDIO_OFFLINE, settings=SETTINGS, logger=log,
)
# Pictures are drawn behind the same proxy as the local AI (``sd_proxy_base_url`` is
# ``llm_local_url``), so they share its breaker and only the sentence differs.
IMAGES = LLM

BACKENDS: dict[str, BackendHealth] = {b.name: b for b in (LLM, ARCADE, AUDIO)}

#: What each feature still does while the local AI is offline (for the status endpoint).
STILL_WORKING = ["home control", "time and date", "weather", "timers", "music playback", "chat with family"]


def configure(*, llm_url: str = "", arcade_url: str = "", audio_url: str = "") -> None:
    """Point each backend at its configured address (from settings and .env, never a literal)."""
    for health, url in ((LLM, llm_url), (ARCADE, arcade_url), (AUDIO, audio_url)):
        if url:
            health.set_url(url)


_WAITING_LOCK = threading.Lock()
_WAITING: set[str] = set()
#: Who is told "it's back": not the shared household identity or system accounts.
_NOT_A_PERSON = {"", "household", "guest", "raven_system", "raven_admin", "system"}


def remember_waiting(user: str | None) -> None:
    """``user`` was told "I'll let you know when it's back": note it for the recovery notice."""
    name = str(user or "").strip()
    if name.lower() in _NOT_A_PERSON:
        return
    with _WAITING_LOCK:
        _WAITING.add(name)


def take_waiting() -> list[str]:
    """Everyone waiting for the recovery notice, emptying the list."""
    with _WAITING_LOCK:
        users = sorted(_WAITING)
        _WAITING.clear()
    return users


def back_notice(user: str, error_id: str = "") -> dict[str, Any]:
    """The "thinking brain is back" notice for one person (sent once per outage)."""
    from services.common import notify

    return notify.notice(
        user, "jobs", BACK_TITLE, BACK_BODY, link=notify.link("home"), tag="local_ai:back",
        once=f"local_ai:back:{error_id}" if error_id else None,
    )


def start_watching() -> None:
    """Start every configured backend's probe thread (the gateway's startup)."""
    for health in BACKENDS.values():
        if health.url:
            health.start()


def uses_local_llm(settings: dict[str, Any]) -> bool:
    """Whether chat goes to the local model (the only provider this breaker guards)."""
    from services.gateway.llm_providers import CLOUD_PROVIDER_DEFAULT_URLS

    return str(settings.get("active_llm_provider") or "ollama") not in CLOUD_PROVIDER_DEFAULT_URLS


def llm_down(settings: dict[str, Any] | None = None) -> bool:
    """True when chat would go to the local model and it is known to be offline."""
    if settings is not None and not uses_local_llm(settings):
        return False
    return LLM.is_down()


def offline_error(health: BackendHealth = LLM) -> BackendUnavailable:
    return health.unavailable()


def offline_body(exc: BackendUnavailable, *, voice: bool = False, message: str = "") -> dict[str, Any]:
    """The JSON fields every offline answer carries, whatever shape it is in.

    ``message`` overrides the backend's own sentence (a picture request is refused
    by the local AI's breaker but is told about pictures)."""
    message = message or (VOICE_OFFLINE if voice and exc.backend == LLM.name else exc.message)
    return {
        "status": "ERROR",
        "message": message,
        "error": message,
        "error_code": f"{exc.backend}_offline",
        "error_id": exc.error_id,
        "offline": exc.backend,
        "retryable": True,
    }


def offline_response(health: BackendHealth | BackendUnavailable = LLM, *, status_code: int = 503,
                     message: str = "", **extra: Any) -> JSONResponse:
    """A 503 with the friendly message, for a route that answers ``{status, message}``."""
    exc = health if isinstance(health, BackendUnavailable) else health.unavailable()
    retry_after = str(int(SETTINGS.probe_interval))
    return JSONResponse(status_code=status_code, content={**offline_body(exc, message=message), **extra},
                        headers={"Retry-After": retry_after})


def status_snapshot() -> dict[str, Any]:
    """Every backend's state for ``GET /api/status/local-ai``: no hosts, no exception text."""
    backends = {name: h.snapshot() for name, h in BACKENDS.items() if h.url or h.is_down()}
    llm = LLM.snapshot()
    return {
        "local_ai": {
            "state": llm["state"],
            "offline": llm["state"] == "down",
            "since": llm["since"],
            "message": CHAT_OFFLINE if llm["state"] == "down" else None,
            "still_working": STILL_WORKING,
        },
        "backends": backends,
        "any_offline": any(b["state"] == "down" for b in backends.values()),
    }


__all__ = [
    "ARCADE",
    "AUDIO",
    "BACKENDS",
    "IMAGES",
    "LLM",
    "BackendUnavailable",
    "configure",
    "is_unreachable",
    "llm_down",
    "offline_body",
    "offline_error",
    "offline_response",
    "start_watching",
    "status_snapshot",
    "uses_local_llm",
]
