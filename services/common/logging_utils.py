"""Logging helpers shared by the services."""

from __future__ import annotations

import logging
import re
import time

#: How long a repeated failure stays quiet before it is worth saying again.
DEFAULT_WARN_COOLDOWN_S = 900

_last_warned: dict[str, float] = {}


def warn_periodically(
    logger: logging.Logger,
    key: str,
    message: str,
    *args: object,
    cooldown_s: float = DEFAULT_WARN_COOLDOWN_S,
) -> None:
    """Warn about a repeated failure, but not on every attempt.

    A dependency that is down for an hour used to be invisible (logged at
    debug) or a flood (a warning per poll). The first failure is a warning,
    repeats inside the cooldown drop to debug, and it warns again once the
    cooldown expires so a long outage stays visible.

    ``key`` identifies the failure so two unrelated ones do not silence each
    other; ``message``/``args`` are the usual logging pair.
    """
    now = time.monotonic()
    # `None` means "never warned", not "warned at monotonic 0". Using 0.0 as the
    # sentinel meant the first failure only warned once the machine had been up
    # for the whole cooldown: on a freshly booted host (a CI runner, a just
    # restarted box) the first real failure logged at debug.
    last = _last_warned.get(key)
    if last is None or now - last >= cooldown_s:
        _last_warned[key] = now
        logger.warning(message, *args)
    else:
        logger.debug(message, *args)


#: Query parameters whose values are credentials. A WebSocket or a webhook can
#: only carry its secret in the URL, and uvicorn's access log writes the whole
#: URL: the git-pull webhook token and the media token (``mt``) were both found
#: in plain text in the service logs (2026-11-13).
SECRET_QUERY_KEYS = ("token", "mt", "api_key", "access_token", "code", "key", "secret", "password")

_SECRET_QUERY_RE = re.compile(r"([?&](?:" + "|".join(SECRET_QUERY_KEYS) + r")=)[^&#\s\"]*", re.IGNORECASE)


def redact_query_secrets(text: str) -> str:
    """``text`` with the value of every credential query parameter replaced by ``***``."""
    return _SECRET_QUERY_RE.sub(r"\1***", text) if "=" in text else text


class _RedactQuerySecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact_query_secrets(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact_query_secrets(record.msg)
        return True


def redact_access_log() -> None:
    """Mask credential query parameters in uvicorn's access log. Call once at import."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactQuerySecrets) for f in access.filters):
        access.addFilter(_RedactQuerySecrets())
