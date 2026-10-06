"""Every gateway route must be reachable through Caddy.

Caddy sends some prefixes to other services (/api/devices* to identity,
/api/admin* to the workspace runtime). A gateway route added under one of
them without its own `handle` is unreachable from outside: in production
/api/devices/favorites and /api/devices/pair answered 405, and an admin's
star grant and the admin DNS and volume routes 404, while their tests passed.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Reached by other means than the public site, so Caddy need not route them.
NOT_PUBLIC = {
    "/ports/expose", "/ports/list/{workspace_id}",  # workspace containers call the gateway directly
}


def _handles() -> list[tuple[str, str]]:
    """(path pattern, upstream service) for the Caddyfile's handles."""
    text = (ROOT / "Caddyfile").read_text()
    return [(path, up) for path, up in re.findall(r"handle (/\S*) \{\s*reverse_proxy (\w+):", text)]


def _upstream(path: str, handles: list[tuple[str, str]]) -> str | None:
    best = None
    for pattern, upstream in handles:
        rx = "^" + re.escape(pattern.rstrip("*")).replace(r"\*", "[^/]+")
        rx += "" if pattern.endswith("*") else "$"
        if re.match(rx, path) and (best is None or len(pattern) > len(best[0])):
            best = (pattern, upstream)
    return best[1] if best else None


def _routes(service: str) -> list[str]:
    text = "".join(p.read_text() for p in (ROOT / "services" / service).glob("*.py"))
    return re.findall(r'@app\.(?:get|post|put|patch|delete|api_route|websocket)\(\s*"([^"]+)"', text)


def _matches(path: str, route: str) -> bool:
    rx = re.sub(r"\\\{[^}]+:path\\\}", ".+", re.escape(route))
    rx = re.sub(r"\\\{[^}]+\\\}", "[^/]+", rx)
    return re.match("^" + rx + "$", path) is not None


def test_gateway_routes_are_reachable_through_caddy():
    handles = _handles()
    unreachable = []
    for route in sorted(set(_routes("gateway")) - NOT_PUBLIC):
        sample = re.sub(r"\{[^}]+\}", "x", route)
        upstream = _upstream(sample, handles)
        if upstream in (None, "gateway"):
            continue
        service = {"workspace_runtime": "workspace_runtime"}.get(upstream, upstream)
        if (ROOT / "services" / service).is_dir() and any(_matches(sample, r) for r in _routes(service)):
            continue  # the other service serves the same path
        unreachable.append(f"{route} -> {upstream}")
    assert not unreachable, "Caddy sends these gateway routes elsewhere: " + ", ".join(unreachable)
