"""Where a tapped notice goes (docs/PUSH.md, "Deep links").

Every Jarvis notice carries ``data.link``: a structured target built here and
nowhere else::

    {"route": "member", "path": "/family/member/sam", "query": {"section": "time"},
     "hash": "", "focus": "unlock-21", "guard": "parent",
     "href": "/family/member/sam?section=time&focus=unlock-21"}

- ``route`` names an entry of ``ROUTES``, the pages of the app a notice may
  open. ``path`` is that page, with its placeholders filled; ``query`` holds only
  the parameters the page reads; ``hash`` only the anchors it knows.
- ``focus`` is the one item on the page the notice is about (a message, a
  mission, a request, a plant): the page scrolls to the element marked
  ``data-focus-id`` with that value and highlights it, or says the item is gone.
  It is ``<kind>-<id>``, and the route lists the kinds it can show.
- ``guard`` is who the page is for. It is informational: the app applies its own
  route guards (admin pages, a Beloved chat, a locked private chat) to a link
  exactly as to any other navigation, so a link never opens more than a tap on
  the page itself would.
- ``href`` is all of it as one in-app address. It is also sent as ``data.url``,
  which older apps follow.

``link()`` builds one and refuses anything a client could not open: an unknown
route, a parameter or anchor the page does not read, a focus kind the page does
not show. ``link_from_href()`` reads an address back into a link (old senders,
scripts, a view button). The routes are mirrored for the web and Android apps
in ``services/ui/src/lib/noticeRoutes.json`` (``write_routes_json``); a test
keeps the two equal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

#: The query parameter that names the item to scroll to and highlight.
FOCUS_PARAM = "focus"

GUARDS = ("member", "admin", "parent", "beloved", "private_chat", "hub")

_FOCUS = re.compile(r"^([a-z][a-z_]{0,31})-([A-Za-z0-9_.:@~-]{1,160})$")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.@~-]{1,160}$")
_HASH = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class LinkError(ValueError):
    """A notice target no client could open."""


@dataclass(frozen=True)
class Route:
    """One page a notice may open."""

    path: str
    query: frozenset[str] = frozenset()
    hashes: frozenset[str] = frozenset()
    #: Anchors the page understands by shape rather than by name (a verse: ``v16``).
    hash_pattern: str = ""
    focus: frozenset[str] = frozenset()
    guard: str = "member"
    #: A query parameter that makes the page stricter (``beloved=1``): its guard.
    query_guards: dict[str, str] = field(default_factory=dict)
    #: Allowed values for a path placeholder or query parameter, where the page
    #: only knows a fixed set (an admin tab, a family tab).
    choices: dict[str, frozenset[str]] = field(default_factory=dict)
    description: str = ""


def _r(path: str, *, query=(), hashes=(), hash_pattern: str = "", focus=(), guard: str = "member",
       query_guards=None, choices=None, description: str = "") -> Route:
    return Route(path, frozenset(query), frozenset(hashes), hash_pattern, frozenset(focus), guard,
                 dict(query_guards or {}), {k: frozenset(v) for k, v in (choices or {}).items()}, description)


ADMIN_SECTIONS = ("users", "groups", "hardware", "monitor", "storage", "network", "intercom", "ops",
                  "integrations", "database", "services", "bible")
FAMILY_TABS = ("chat", "calls", "announce", "games", "create")
MEMBER_SECTIONS = ("overview", "comms", "time", "apps", "activity", "location", "health", "faith", "devices")
DEVICE_TABS = ("devices", "add", "places", "setup", "family")
#: The older "#devices-..." anchors the Devices page maps onto its tabs (pages/Devices.tsx).
DEVICE_ANCHORS = ("devices-hardware", "devices-light-groups", "devices-duplicates", "devices-new", "add-fix",
                  "devices-areas", "devices-labels", "devices-zones", "devices-integrations", "devices-phones",
                  "devices-persons", "devices-logins", "devices-rooms", "devices-archived", "devices-family",
                  "devices-windows", "devices-folders", "devices-activity")
HEALTH_TABS = ("today", "fitness", "sleep", "nutrition", "health")
# Actual tabs in the UI (services/ui/src/pages/Bible.tsx TAB_IDS):
# read, today, play, memorize, studies, progress
# Old values kept so existing links still validate.
BIBLE_TABS = ("read", "today", "plans", "memorize", "play", "study", "notes", "devotional",
              "studies", "progress")

#: Every page a notice may open, by name. Adding a notice that needs a page not
#: listed here means adding the page here (and running ``write_routes_json``).
ROUTES: dict[str, Route] = {
    "home": _r("/", description="The dashboard"),
    "family": _r("/family", query=("tab", "answer", "video", "drop_in", "play", "room"),
                 choices={"tab": FAMILY_TABS}, description="A tab of the Family page; Announce lists timers"),
    "chat": _r("/family", query=("chat", "beloved", "private"), focus=("msg",),
               query_guards={"beloved": "beloved", "private": "private_chat"},
               description="One family conversation; focus is the message"),
    "member": _r("/family/member/{username}", query=("section",), choices={"section": MEMBER_SECTIONS},
                 focus=("unlock", "guard", "time", "app", "concern", "chore"),
                 guard="parent", description="A family member's page (a parent's view of a child)"),
    "guardian": _r("/guardian", query=("member", "area", "week", "child"), focus=("recap",),
                   choices={"area": ("all", "overview", "heart", "time", "safety")},
                   guard="parent", description="The guardian hub"),
    "wander": _r("/wander", query=("person", "trips", "at", "by"), description="The family map, at a person"),
    "health": _r("/fitness", query=("tab",), choices={"tab": HEALTH_TABS},
                 description="The health hub (steps, fasting, screen time, chores)"),
    "calendar": _r("/calendar", query=("date",), description="The calendar, on a day"),
    # A reading reminder opens the whole chapter at its verse: ``ref`` (or
    # book/chapter/verse) in the query and the verse as ``#v16``. The reader
    # scrolls to and highlights the verse itself.
    # Study books use ?tab=studies&book=<id>&section=<id>&ordinal=<n> with
    # an optional #p-<id> anchor for a paragraph.
    "bible": _r("/bible", query=("tab", "ref", "book", "chapter", "verse", "translation", "plan", "group", "day",
                                  "section", "ordinal"),
                choices={"tab": BIBLE_TABS}, hash_pattern=r"^(v|verse-?)[0-9]{1,3}(-[0-9]{1,3})?$|^p-[a-z0-9]{1,32}$",
                description="The Bible reader"),
    "garden": _r("/garden", query=("tag",), focus=("plant",), description="The garden, at a plant"),
    "plant": _r("/garden/plants/{id}", query=("journal",), description="One plant"),
    "devices": _r("/devices", hashes=DEVICE_TABS + DEVICE_ANCHORS, focus=("discovery", "entry"),
                  description="Devices: a found device (Add), an integration (Setup), the family's (Family)"),
    "automations": _r("/automations", guard="hub", description="Automations and the hub's repairs"),
    "history": _r("/history", query=("e", "view", "area"), guard="hub", description="One device's history"),
    "cameras": _r("/cameras", query=("camera", "view"), guard="hub", description="Cameras"),
    "rooms": _r("/rooms", guard="hub", description="Rooms"),
    "climate": _r("/climate", guard="hub", description="Climate"),
    "energy": _r("/energy", guard="hub", description="Energy"),
    "lists": _r("/lists", query=("list",), guard="hub", description="Lists"),
    "notes": _r("/notes", query=("note",), description="Notes, at a note"),
    "recipes": _r("/recipes", query=("tab", "recipe"), description="Recipes"),
    "media": _r("/media", description="Media"),
    "workspaces": _r("/workspaces", query=("select",), description="Workspaces, at a workspace"),
    "artifacts": _r("/artifacts", description="Artifacts (generated files)"),
    "knowledge": _r("/knowledge", description="Knowledge"),
    "settings": _r("/settings", hashes=("family-drop-in",), focus=("section", "report"),
                   description="Settings, at a section or a report"),
    "identity": _r("/identity", query=("spotify",), focus=("voice",),
                   choices={"spotify": ("linked", "failed", "cancelled")},
                   description="Accounts and integrations; voice recordings waiting for an answer"),
    "admin": _r("/admin/{section}", query=("mission",), choices={"section": ADMIN_SECTIONS}, focus=("mission",),
                guard="admin", description="An admin tab; Raven Ops opens a mission with ?mission="),
    "lab": _r("/lab", query=("tab",), choices={"tab": ("tests", "missions", "training")}, guard="admin",
              description="Jarvis Lab"),
}


def _fill(route: Route, name: str, params: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        value = params.pop(key, None)
        if value is None or str(value) == "":
            raise LinkError(f"route {name!r} needs {key!r}")
        text = str(value)
        if not _SEGMENT.match(text):
            raise LinkError(f"{key!r} is not a page address part: {text!r}")
        allowed = route.choices.get(key)
        if allowed is not None and text not in allowed:
            raise LinkError(f"{key!r} must be one of {sorted(allowed)}, not {text!r}")
        return quote(text, safe="@~.-_")

    return _PLACEHOLDER.sub(sub, route.path)


def _href(path: str, query: dict[str, str], focus: str, hash_: str) -> str:
    q = dict(query)
    if focus:
        q[FOCUS_PARAM] = focus
    out = path
    if q:
        out += "?" + urlencode(q, quote_via=quote)
    if hash_:
        out += "#" + hash_
    return out


def link(route: str, *, focus: str | None = None, hash: str | None = None, **params: Any) -> dict[str, Any]:
    """The ``data.link`` of a notice that opens ``route``.

    Path placeholders and query parameters are both keyword arguments; ``None``
    values are left out. ``focus`` is the item (``"msg-42"``), ``hash`` a
    section anchor the page knows. Raises ``LinkError`` for anything the page
    could not open.
    """
    spec = ROUTES.get(route)
    if spec is None:
        raise LinkError(f"unknown route {route!r}")
    rest = {k: v for k, v in params.items() if v is not None and v != ""}
    path = _fill(spec, route, rest)
    query: dict[str, str] = {}
    for key, value in rest.items():
        if key not in spec.query:
            raise LinkError(f"route {route!r} does not read {key!r}")
        text = "1" if value is True else str(value)
        allowed = spec.choices.get(key)
        if allowed is not None and text not in allowed:
            raise LinkError(f"{key!r} must be one of {sorted(allowed)}, not {text!r}")
        query[key] = text[:200]
    focus = (focus or "").strip()
    if focus:
        m = _FOCUS.match(focus)
        if not m:
            raise LinkError(f"focus must be <kind>-<id>, not {focus!r}")
        if m.group(1) not in spec.focus:
            raise LinkError(f"route {route!r} cannot show a {m.group(1)!r}")
    hash_ = (hash or "").strip().lstrip("#")
    if hash_ and (not _HASH.match(hash_) or (
            hash_ not in spec.hashes and not (spec.hash_pattern and re.match(spec.hash_pattern, hash_)))):
        raise LinkError(f"route {route!r} has no section {hash_!r}")
    guard = spec.guard
    for key, strict in spec.query_guards.items():
        if key in query:
            guard = strict
    return {
        "route": route,
        "path": path,
        "query": query,
        "hash": hash_,
        "focus": focus,
        "guard": guard,
        "href": _href(path, query, focus, hash_),
    }


def focus_id(kind: str, item: Any) -> str:
    """``focus_id("msg", 42)`` is ``"msg-42"``: the value a page marks the item with."""
    return f"{kind}-{item}"


def _match(spec: Route, path: str) -> dict[str, str] | None:
    want = spec.path.split("/")
    have = path.split("/")
    if len(want) != len(have):
        return None
    out: dict[str, str] = {}
    for w, h in zip(want, have, strict=True):
        m = _PLACEHOLDER.fullmatch(w)
        if m:
            if not h:
                return None
            out[m.group(1)] = unquote(h)
        elif w != h:
            return None
    return out


def link_from_href(href: str) -> dict[str, Any]:
    """Read an in-app address (``/family?chat=abc``) back into a link.

    Each route whose page matches is tried in order; the first that accepts
    every parameter wins. Raises ``LinkError`` when none does, so an address no
    page reads is refused instead of passed on.
    """
    text = str(href or "").strip()
    if not text.startswith("/") or text.startswith("//"):
        raise LinkError(f"not an in-app address: {text!r}")
    parts = urlsplit(text)
    path = parts.path.rstrip("/") or "/"
    pairs = parse_qsl(parts.query, keep_blank_values=False)
    focus = ""
    query: dict[str, str] = {}
    for key, value in pairs:
        if key == FOCUS_PARAM:
            focus = value
        else:
            query[key] = value
    errors = []
    for name, spec in ROUTES.items():
        placeholders = _match(spec, path)
        if placeholders is None:
            continue
        try:
            return link(name, focus=focus or None, hash=parts.fragment or None, **placeholders, **query)
        except LinkError as e:
            errors.append(f"{name}: {e}")
    raise LinkError(f"no page reads {text!r}" + (f" ({'; '.join(errors)})" if errors else ""))


def check(value: Any) -> dict[str, Any]:
    """A link as received (``data.link``), rebuilt from its parts so only what
    ``link()`` accepts survives. Raises ``LinkError``."""
    if not isinstance(value, dict):
        raise LinkError("link must be an object")
    route = str(value.get("route") or "")
    spec = ROUTES.get(route)
    if spec is None:
        raise LinkError(f"unknown route {route!r}")
    params = _match(spec, str(value.get("path") or ""))
    if params is None:
        raise LinkError(f"{value.get('path')!r} is not the {route!r} page")
    query = value.get("query") or {}
    if not isinstance(query, dict):
        raise LinkError("link query must be an object")
    return link(route, focus=str(value.get("focus") or "") or None, hash=str(value.get("hash") or "") or None,
                **params, **{str(k): v for k, v in query.items()})


def routes_table() -> dict[str, Any]:
    """The routes as the clients read them (noticeRoutes.json)."""
    return {
        "focus_param": FOCUS_PARAM,
        "routes": {
            name: {
                "path": spec.path,
                "query": sorted(spec.query),
                "hashes": sorted(spec.hashes),
                "hash_pattern": spec.hash_pattern,
                "focus": sorted(spec.focus),
                "guard": spec.guard,
                "query_guards": dict(sorted(spec.query_guards.items())),
                "choices": {k: sorted(v) for k, v in sorted(spec.choices.items())},
                "description": spec.description,
            }
            for name, spec in ROUTES.items()
        },
    }


ROUTES_JSON = Path(__file__).resolve().parents[1] / "ui" / "src" / "lib" / "noticeRoutes.json"


def write_routes_json(path: Path = ROUTES_JSON) -> None:
    """Regenerate the clients' copy: ``python -m services.common.notice_links``."""
    path.write_text(json.dumps(routes_table(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    write_routes_json()
    print(f"wrote {ROUTES_JSON}")
