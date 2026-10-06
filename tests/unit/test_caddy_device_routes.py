"""Every gateway route under /api/devices must reach the gateway.

Caddy sends /api/devices* to identity, so a gateway route added there without
its own `handle` was unreachable from outside: /api/devices/favorites and
/api/devices/pair answered 405 in production while their tests passed.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _handles() -> dict[str, str]:
    """Path prefix -> upstream service, for the API handles in the Caddyfile."""
    text = (ROOT / "Caddyfile").read_text()
    found: dict[str, str] = {}
    for path, body in re.findall(r"handle (/api/devices[^\s{]*) \{(.*?)\}", text, re.S):
        upstream = re.search(r"reverse_proxy (\w+):", body)
        if upstream:
            found[path.rstrip("*")] = upstream.group(1)
    return found


def _upstream(path: str, handles: dict[str, str]) -> str | None:
    best = max((p for p in handles if path.startswith(p)), key=len, default=None)
    return handles[best] if best else None


def test_gateway_device_routes_are_routed_to_the_gateway():
    gateway = (ROOT / "services/gateway/main.py").read_text()
    identity = (ROOT / "services/identity/main.py").read_text()
    route = r'@app\.\w+\("(/api/devices/[^"{]+)'
    identity_paths = set(re.findall(route, identity))
    handles = _handles()
    for path in sorted(set(re.findall(route, gateway)) - identity_paths):
        assert _upstream(path, handles) == "gateway", f"{path} is routed to {_upstream(path, handles)}"
