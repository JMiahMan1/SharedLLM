"""Derive Jarvis's feature catalogue from the code, so it cannot go stale.

A hand-written list of "things Jarvis can do" is wrong the first time somebody
adds a route. This module reads the source instead:

* every FastAPI route decorator (``@app.post("/files/write")``) in every
  service's ``main.py`` is parsed with :mod:`ast` -- no service is imported and
  no container has to be running;
* the gateway's own routes come from the live ``app`` the caller passes in, so
  the Jarvis API surface is exact (including routers included at import time);
* the Raven tool table (``tool_registry._RAVEN_TOOL_TABLE``) supplies the tool
  names the model is actually offered;
* the UI's routes are read from ``App.tsx`` so the workspace screens are part of
  the same picture.

Each :class:`Feature` carries a derived, plain-English use case -- "Ask Jarvis
to write a workspace file" -- which the curriculum turns into an evaluation
prompt. Adding a route therefore adds a capability the trainer knows about
without anybody editing a list.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import pathlib
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger("gateway.trainer.catalog")

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Where each service declares its routes. Missing files are skipped, so a
#: service can be added or renamed without breaking the catalogue.
SERVICE_ROUTE_FILES: dict[str, tuple[str, ...]] = {
    "gateway": ("services/gateway/main.py",),
    "workspace_runtime": ("services/workspace_runtime/main.py",),
    "rag": ("services/rag/main.py",),
    "execution": ("services/execution/main.py",),
    "identity": ("services/identity/main.py",),
    "storage": ("services/storage/main.py",),
    "logging": ("services/logging/main.py",),
    "geo": ("services/geo/main.py",),
    "bible": ("services/bible/main.py",),
    "health": ("services/health/main.py",),
    "garden": ("services/garden/main.py",),
    "automation": ("services/automation/main.py",),
    "telemetry": ("services/telemetry/main.py",),
}

_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}

#: Objects a route decorator hangs off. Anything else (a test double, a
#: sub-application's ``mount``) is not a route definition.
_ROUTE_OWNERS = {"app", "router", "api", "bp", "application"}

#: Paths that are framework plumbing, not capabilities a person uses.
_SKIP_PATHS = {
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
    "/metrics",
}

#: Longest capability tag list for one route; keeps the catalogue readable.
_MAX_TAGS = 4

_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("workspaces", ("/workspace", "/workspaces", "/files", "/git", "/ports", "/terminal")),
    ("memory", ("/rag", "/dream", "/learn", "/recall", "/memory", "/embed")),
    ("tools", ("/execute", "/tool")),
    ("media", ("/media", "/music", "/audiobook", "/podcast", "/tv", "/speaker", "/volume", "/stt", "/tts")),
    ("home", ("/light", "/climate", "/ha_", "/entity", "/scene", "/switch", "/cover", "/lock")),
    ("bible", ("/bible", "/passage", "/scripture", "/devotional", "/memorize")),
    ("family", ("/family", "/talk", "/chat", "/guardian", "/member", "/private")),
    ("health", ("/health", "/fitness", "/steps", "/sleep", "/nutrition", "/workout")),
    ("garden", ("/garden", "/plant", "/harvest", "/preservation")),
    ("geo", ("/geo", "/location", "/trip", "/place", "/route")),
    ("calendar", ("/calendar", "/schedule", "/skylight", "/event")),
    ("notes", ("/note", "/notes", "/recipe", "/cookbook", "/chore", "/meal")),
    ("images", ("/image", "/camera", "/ocr", "/drawing", "/vision")),
    ("identity", ("/user", "/users", "/auth", "/credential", "/role", "/device")),
    ("automation", ("/automation", "/timer", "/trigger", "/hook")),
    ("telemetry", ("/telemetry", "/report", "/notification", "/notice")),
    ("admin", ("/admin",)),
)

_VERB_TEMPLATES = {
    "GET": "Show {what}",
    "POST": "Ask Jarvis to {what}",
    "PUT": "Ask Jarvis to update {what}",
    "PATCH": "Ask Jarvis to update {what}",
    "DELETE": "Ask Jarvis to remove {what}",
    "WEBSOCKET": "Stream {what}",
}

#: Path words that add nothing to a human sentence.
_FILLER = {"api", "execute", "internal", "v1", "backend", "svc", "service"}

#: Plural segment names that read badly at the start of a use case.
_SINGULAR = {"workspaces": "workspace", "notes": "note", "users": "user"}



@dataclass(frozen=True)
class Feature:
    """One capability Jarvis has, with the phrase a person would use for it."""

    id: str
    kind: str  # route | tool | page
    service: str
    method: str
    path: str
    name: str
    summary: str
    use_case: str
    tags: tuple[str, ...] = ()
    auth: str = "user"  # public | user | admin | internal
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tags_for(path: str) -> tuple[str, ...]:
    """Capability tags a path belongs to, in rule order, de-duplicated."""
    low = path.lower()
    found: list[str] = []
    for tag, needles in _TAG_RULES:
        if tag in found:
            continue
        if any(needle in low for needle in needles):
            found.append(tag)
    return tuple(found[:_MAX_TAGS])


def _human_path(path: str) -> str:
    """``/api/workspaces/{id}/files/write`` -> ``workspace files write``."""
    without_placeholders = re.sub(r"\{[^}]*\}", " ", path)
    words = [
        part.strip()
        for part in re.split(r"[/_\-]+", without_placeholders.strip("/"))
        if part.strip() and part.strip().lower() not in _FILLER
    ]
    if words:
        words[0] = _SINGULAR.get(words[0].lower(), words[0])
    return " ".join(words) or "this capability"


def use_case_for(method: str, path: str) -> str:
    """The sentence the curriculum asks a person to type (and the model to route)."""
    template = _VERB_TEMPLATES.get(method.upper(), "Use {what}")
    return template.format(what=_human_path(path))


def auth_for(service: str, path: str) -> str:
    """Who may call it: the gateway's own routes are user/admin, the rest internal."""
    if service != "gateway":
        return "internal"
    if path.startswith("/api/admin"):
        return "admin"
    if path.startswith("/api") or path.startswith("/execute"):
        return "user"
    return "public"


def feature_id(service: str, method: str, path: str) -> str:
    """Stable id: the same route keeps the same id across restarts."""
    return f"{service}:{method.lower()}:{path}"


def _first_sentence(doc: str | None) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line.rstrip(".")[:200]
    return ""


def _route_from_decorator(node: ast.AST) -> tuple[str, str] | None:
    """(METHOD, path) when ``node`` is ``@app.get("/x")``-shaped."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr.lower() not in _HTTP_VERBS:
        return None
    owner = func.value
    owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    if owner_name not in _ROUTE_OWNERS:
        return None
    if not node.args:
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    return func.attr.upper(), first.value


def parse_routes(path: pathlib.Path) -> list[tuple[str, str, str, str]]:
    """Routes declared in one file: ``(method, route_path, function_name, summary)``.

    Parsing the AST (rather than importing the module) keeps this usable on a
    developer machine with no containers running, and keeps it cheap enough to
    call per request.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:  # pragma: no cover - unreadable file
        log.debug("[trainer.catalog] cannot read %s: %s", path, exc)
        return []

    routes: list[tuple[str, str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        summary = _first_sentence(ast.get_docstring(node))
        for decorator in node.decorator_list:
            parsed = _route_from_decorator(decorator)
            if parsed:
                method, route_path = parsed
                routes.append((method, route_path, node.name, summary))
    return routes


def service_features(service: str, files: Iterable[str]) -> list[Feature]:
    """Every route a service declares, as :class:`Feature` items."""
    features: list[Feature] = []
    for relative in files:
        file_path = ROOT / relative
        for method, route_path, function_name, summary in parse_routes(file_path):
            if route_path in _SKIP_PATHS:
                continue
            features.append(
                Feature(
                    id=feature_id(service, method, route_path),
                    kind="route",
                    service=service,
                    method=method,
                    path=route_path,
                    name=function_name,
                    summary=summary,
                    use_case=use_case_for(method, route_path),
                    tags=tags_for(route_path),
                    auth=auth_for(service, route_path),
                    source=relative,
                )
            )
    return features


def _iter_routes(routes: Any) -> Any:
    """Flatten FastAPI's deferred includes (``_IncludedRouter``) into real routes.

    Recent FastAPI wraps ``include_router`` in a deferred object that has no
    ``path`` until the router is built. Walking ``original_router.routes`` is how
    the live app's routes are seen without waiting for the first request.
    """
    for route in routes or []:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_routes(getattr(inner, "routes", []))
            continue
        yield route


def gateway_features(app: Any) -> list[Feature]:
    """The live gateway app's routes -- the Jarvis API as it actually is.

    ``app`` is passed in (``request.app``) instead of imported so this module
    never imports ``services.gateway.main``: the router is included by main, and
    a top-level import here would be an import cycle.
    """
    features: list[Feature] = []
    for route in _iter_routes(getattr(app, "routes", [])):
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None)
        if not path or path in _SKIP_PATHS:
            continue
        endpoint = getattr(route, "endpoint", None)
        name = getattr(route, "name", "") or getattr(endpoint, "__name__", "")
        summary = _first_sentence(inspect.getdoc(endpoint) if endpoint else "")
        for method in sorted(methods or {"WEBSOCKET" if "websocket" in type(route).__name__.lower() else "GET"}):
            if method in {"HEAD", "OPTIONS"}:
                continue
            features.append(
                Feature(
                    id=feature_id("gateway", method, path),
                    kind="route",
                    service="gateway",
                    method=method,
                    path=path,
                    name=name,
                    summary=summary,
                    use_case=use_case_for(method, path),
                    tags=tags_for(path),
                    auth=auth_for("gateway", path),
                    source="services/gateway (live app)",
                )
            )
    return features


def tool_features() -> list[Feature]:
    """The tools the model is offered, from the one table that defines them."""
    try:
        from services.gateway.tool_registry import _RAVEN_TOOL_TABLE
    except Exception as exc:  # pragma: no cover - import cost only
        log.warning("[trainer.catalog] tool table unavailable: %s", exc)
        return []

    features: list[Feature] = []
    for row in _RAVEN_TOOL_TABLE:
        name, service, method, path = row[0], row[1], row[2], row[3]
        summary = str(row[5]) if len(row) > 5 else ""
        features.append(
            Feature(
                id=f"tool:{_slug(str(name))}",
                kind="tool",
                service=str(service),
                method=str(method).upper(),
                path=str(path),
                name=str(name),
                summary=_first_sentence(summary),
                use_case=f"Call {name} for: {_first_sentence(summary) or 'this task'}",
                tags=tags_for(str(path)),
                auth="user",
                source="services/gateway/tool_registry.py",
            )
        )
    return features


_ROUTE_LINE = re.compile(r'path="([^"]+)".*?<([A-Z][A-Za-z0-9_]*)')


def ui_pages(app_tsx: pathlib.Path | None = None) -> list[Feature]:
    """Screens the Workspace app mounts, read from its router table."""
    path = app_tsx or ROOT / "services" / "ui" / "src" / "App.tsx"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # pragma: no cover - UI absent in some deploys
        log.debug("[trainer.catalog] cannot read App.tsx: %s", exc)
        return []

    pages: list[Feature] = []
    seen: set[str] = set()
    for line in lines:
        if "<Route" not in line:
            continue
        match = _ROUTE_LINE.search(line)
        if not match:
            continue
        route_path, component = match.group(1), match.group(2)
        if route_path in seen or route_path == "*":
            continue
        seen.add(route_path)
        pages.append(
            Feature(
                id=f"page:{_slug(route_path)}",
                kind="page",
                service="ui",
                method="GET",
                path=route_path,
                name=component,
                summary=f"The {component} screen",
                use_case=f"Open {component} at {route_path}",
                tags=tags_for(route_path),
                auth="user",
                source="services/ui/src/App.tsx",
            )
        )
    return pages


def build_catalog(app: Any | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """The whole catalogue: routes (all services), tools, and UI pages.

    ``app`` is the live gateway app when a request is available; without one the
    gateway routes are parsed from source instead, so a script or test can build
    the same catalogue offline. The non-app half is parsed once and cached: the
    gateway's own ``main.py`` is over twenty thousand lines, and its routes do
    not change while the process is running.
    """
    features: list[Feature] = []
    for service, files in SERVICE_ROUTE_FILES.items():
        if service == "gateway":
            continue
        features.extend(_service_features_cached(service, files, refresh=refresh))
    if app is not None:
        features.extend(gateway_features(app))
    else:
        features.extend(
            _service_features_cached("gateway", SERVICE_ROUTE_FILES["gateway"], refresh=refresh)
        )
    features.extend(_static_features(refresh=refresh))

    by_service: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for feature in features:
        by_service[feature.service] = by_service.get(feature.service, 0) + 1
        by_kind[feature.kind] = by_kind.get(feature.kind, 0) + 1
        for tag in feature.tags:
            by_tag[tag] = by_tag.get(tag, 0) + 1

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fingerprint": _fingerprint(features),
        "counts": {
            "total": len(features),
            "by_service": dict(sorted(by_service.items())),
            "by_kind": dict(sorted(by_kind.items())),
            "by_tag": dict(sorted(by_tag.items())),
        },
        "features": [f.to_dict() for f in features],
    }


_STATIC_CACHE: list[Feature] | None = None
_ROUTE_CACHE: dict[str, list[Feature]] = {}


def _static_features(*, refresh: bool = False) -> list[Feature]:
    """Tools and UI pages -- unchanged for the process's life unless refreshed."""
    global _STATIC_CACHE
    if _STATIC_CACHE is None or refresh:
        _STATIC_CACHE = [*tool_features(), *ui_pages()]
    return list(_STATIC_CACHE)


def _service_features_cached(service: str, files: tuple[str, ...], *, refresh: bool = False) -> list[Feature]:
    """Routes described by source files, parsed once per process.

    A source file cannot gain a route while the process runs, and the gateway's
    ``main.py`` alone is over twenty thousand lines, so re-parsing it per request
    would cost seconds for no new information. ``refresh=True`` re-reads (used by
    a long-running trainer that watches the tree).
    """
    if refresh or service not in _ROUTE_CACHE:
        _ROUTE_CACHE[service] = service_features(service, files)
    return list(_ROUTE_CACHE[service])


def _fingerprint(features: list[Feature]) -> str:
    digest = hashlib.sha1()
    for feature in sorted(features, key=lambda f: f.id):
        digest.update(feature.id.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


def find(catalog: dict[str, Any], service: str | None = None, kind: str | None = None,
         tag: str | None = None) -> list[dict[str, Any]]:
    """A filtered view of a built catalogue (used by the curriculum and the UI)."""
    result = []
    for feature in catalog.get("features", []):
        if service and feature.get("service") != service:
            continue
        if kind and feature.get("kind") != kind:
            continue
        if tag and tag not in (feature.get("tags") or []):
            continue
        result.append(feature)
    return result
