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


#: Names whose value is a credential, in ``name=value``, ``name: value`` or ``"name": "value"``.
_SECRET_NAME = (r"[\w-]*(?:password|passwd|secret|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token"
                r"|auth[_-]?token|private[_-]?key|preshared[_-]?key|psk|token)")
#: Only values that look like a credential (8+ token characters): "tokens: 512" stays readable.
_SECRET_ASSIGN_RE = re.compile(
    r"(?P<k>[\"']?\b" + _SECRET_NAME + r"\b[\"']?\s*[:=]\s*[\"']?)(?P<v>[A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)
_SECRET_HEADER_RE = re.compile(
    r"(?P<k>\b(?:authorization|x-internal-secret|x-api-key|cookie|set-cookie)\b[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?:bearer|basic|token)\s+)?(?P<v>[^\s\"',;\[\]]{6,})", re.IGNORECASE)
_SECRET_SHAPES = [
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),  # a JWT
    re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL),
]


def redact_secrets(text: str, known: tuple[str, ...] | list[str] = ()) -> str:
    """``text`` with anything that looks like a credential replaced by ``***``.

    For log text shown to people (the Admin log viewer): credential query
    parameters, Authorization / X-Internal-Secret / cookie headers, Bearer
    tokens, JWTs, GitHub/GitLab/OpenAI-style keys, PEM private keys,
    ``password=`` / ``"token": "..."`` / ``PrivateKey = ...`` assignments, and
    the exact ``known`` values (the reader's own secrets, 8+ characters).
    A second line of defence: services should not log secrets at all.
    """
    if not text:
        return text
    for value in known:
        if value and len(value) >= 8 and value in text:
            text = text.replace(value, "***")
    text = redact_query_secrets(text)
    text = _SECRET_HEADER_RE.sub(lambda m: m.group("k") + "***", text)
    text = _SECRET_ASSIGN_RE.sub(lambda m: m.group("k") + "***", text)
    for shape in _SECRET_SHAPES:
        text = shape.sub(lambda m: "Bearer ***" if m.group(0)[:6].lower() == "bearer" else "***", text)
    return text


#: Environment variables whose names say they hold a credential.
_SECRET_ENV_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|PSK|_KEY$|CREDENTIAL)", re.I)


def secret_env_values(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """The values of this process's secret-looking environment variables, for ``redact_secrets(known=...)``.

    Read once at service start (the environment is not re-read per call).
    """
    import os

    env = os.environ if environ is None else environ
    return tuple(sorted({v for k, v in env.items() if _SECRET_ENV_NAME.search(k) and v and len(v) >= 8},
                        key=len, reverse=True))


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
