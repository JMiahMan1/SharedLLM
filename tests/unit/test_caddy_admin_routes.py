"""Every /api/admin/<area> the gateway serves must be routed to the gateway.

The public Caddy site sends /api/admin* to workspace_runtime unless a more
specific handle says otherwise. The gateway's camera settings (who can watch,
live video, motion recording) had no such handle, so every one of them
answered 404 from workspace_runtime and the Cameras page could not change them.
"""
import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _site_handles() -> list[tuple[str, str]]:
    """(path pattern, upstream) for each handle in the public :80 site."""
    text = (ROOT / "Caddyfile").read_text()
    site = text[text.index("\n:80 {"):]
    out = []
    for m in re.finditer(r"handle (/\S+) \{\s*(?:#[^\n]*\n\s*)*reverse_proxy (\S+)", site):
        out.append((m.group(1), m.group(2)))
    return out


def _upstream(path: str) -> str | None:
    """Caddy orders single-path handles most specific first: the longest matching pattern wins."""
    matching = [(p, up) for p, up in _site_handles() if fnmatch.fnmatchcase(path, p)]
    return max(matching, key=lambda m: len(m[0]))[1] if matching else None


#: Admin routes deliberately not reachable from outside: setup scripts call
#: them on the service's own port.
INTERNAL_ONLY = {("identity", "seed"), ("identity", "seed-credential")}

UPSTREAM = {"gateway": "gateway:11435", "identity": "identity:8001", "workspace_runtime": "workspace_runtime:8007"}


def _admin_paths(service: str) -> set[str]:
    """Every /api/admin path the service defines, with path parameters filled in."""
    paths = set()
    for source in (ROOT / "services" / service).glob("*.py"):
        for m in re.finditer(r'@(?:app|router)\.(?:get|post|put|delete|patch)\("(/api/admin/[^"]+)"', source.read_text()):
            paths.add(re.sub(r"\{[^}]*\}", "x", m.group(1)))
    return paths


def _area(path: str) -> str:
    return path.split("/")[3]


def test_the_camera_settings_reach_the_gateway():
    for path in ("/api/admin/cameras/camera.porch/viewers", "/api/admin/cameras/live-video", "/api/admin/cameras/recording"):
        assert _upstream(path) == "gateway:11435", path


def test_the_account_audit_reaches_identity():
    assert _upstream("/api/admin/audit") == "identity:8001"


def test_every_admin_route_reaches_a_service_that_serves_it():
    """/api/admin* falls through to workspace_runtime; any other service's admin routes need their own handle."""
    wrong = {}
    for service, upstream in UPSTREAM.items():
        for path in sorted(_admin_paths(service)):
            if (service, _area(path)) in INTERNAL_ONLY:
                continue
            target = _upstream(path)
            served_by = next((s for s, u in UPSTREAM.items() if u == target), None)
            if target != upstream and (served_by is None or path not in _admin_paths(served_by)):
                wrong[f"{service} {path}"] = target
    assert wrong == {}, f"admin routes sent to a service without them: {wrong}"
