"""Logging helpers shared by the services."""

from __future__ import annotations

import logging
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
