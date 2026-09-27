"""URL redaction for logging (BUG-06).

Secrets routinely travel in query strings (``?token=``, ``?api_key=``,
``?access_token=``) — WebSocket URLs, proxy targets, request URLs. Never log
those verbatim: pass URLs through :func:`redact_url` first so the sensitive
values are masked as ``***``.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_QUERY_KEYS = frozenset({"token", "api_key", "access_token"})

_FALLBACK_RE = re.compile(
    r"([?&](?:token|api_key|access_token)=)[^&#]*",
    re.IGNORECASE,
)


def redact_url(url: str) -> str:
    """Return ``url`` with sensitive query-parameter values masked as ``***``.

    Non-sensitive query parameters, the path, host and fragment are preserved
    unchanged. Inputs without a query string are returned as-is. If the URL
    cannot be parsed, a regex fallback masks the sensitive parameters; in the
    worst case the literal ``<redacted>`` is returned so a secret can never
    escape through a logging call.
    """
    if not url or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        redacted = [
            (k, "***" if k.lower() in SENSITIVE_QUERY_KEYS else v)
            for k, v in pairs
        ]
        if redacted == pairs:
            return url
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(redacted, safe="*"), parts.fragment)
        )
    except Exception:
        try:
            masked = _FALLBACK_RE.sub(r"\1***", url)
            if masked != url:
                return masked
            return "<redacted>"
        except Exception:
            return "<redacted>"
