"""The knowledge map: a short, fixed index of where information lives and how to fetch it.

Why it exists. Every chat turn used to arrive with retrieval hits from several
collections whether the question needed them or not: "What's the verse of the
day?" carried fifteen device rows, fifteen capability rows and fifteen lessons
(about 4,900 characters), none of them used. On an 8 GB card that is seconds of
prompt reading on every voice question, and the noise costs accuracy too.

Instead the system prompt carries this map: one line per knowledge source naming
the exact tool that reads it, and one line per tool grouped by area, so the model
knows what exists and fetches what it needs when it needs it. The question then
travels with only the small, intent-gated prefetch (``context_router``).

The map is built from the tool registry, never hand-kept, and is the same text
for a given configuration and role: sorted, no clock, no per-request data. The
model server keeps the fixed start of the prompt warm (alpaca's prompt keeper),
which only works while that start does not change.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from services.gateway import tool_registry as tr

#: Areas in the order they are listed, with the title the model sees.
AREAS: tuple[tuple[str, str], ...] = (
    ("home", "Home and devices"),
    ("family", "Family life (calendar, chores, timers, notes, places, garden)"),
    ("media", "Music, video, audiobooks and voices"),
    ("knowledge", "Knowledge and the web"),
    ("workspace", "Files, workspaces and making things"),
    ("system", "Running Jarvis (admins)"),
)
_AREA_TITLES = dict(AREAS)

#: The area of each tool by name. Workspace, Studio and STL tools are matched by prefix
#: so a new one lands in the right place; anything else unlisted is a test failure
#: (test_knowledge_map.py), never a silent "Other".
TOOL_AREAS: dict[str, str] = {
    # home
    "LightControlRequest": "home", "ClimateRequest": "home", "SecurityRequest": "home",
    "HAServiceRequest": "home", "EntitySearchRequest": "home", "LogbookRequest": "home",
    "AnnouncementRequest": "home", "DocumentBroadcastRequest": "home", "NightModeRequest": "home",
    "DropInRequest": "home",
    "TodoListRequest": "home",
    # family
    "CalendarRequest": "family", "ChoreRequest": "family", "TimerRequest": "family",
    "MealPlanRequest": "family", "NoteRequest": "family", "TalkRequest": "family",
    "LocationRequest": "family", tr.TOOL_GARDEN_SUMMARY: "family",
    tr.TOOL_GARDEN_LOG_CARE: "family", tr.TOOL_GARDEN_LOG_HARVEST: "family",
    # media
    "MediaPlayRequest": "media", "MediaTransportRequest": "media", "MediaStatusRequest": "media",
    "VideoPlayRequest": "media", "TVCastRequest": "media", "AudiobookshelfRequest": "media",
    "TTSRequest": "media", "StorageTextToAudioRequest": "media", "AudiobookRegenerateRequest": "media",
    "PodcastRenderRequest": "media", "SpeakerIdentifyRequest": "media", "ListVoicesRequest": "media",
    # knowledge
    "BibleRequest": "knowledge", "CalibreRequest": "knowledge", "ContextSearchRequest": "knowledge",
    "WebSearchRequest": "knowledge", "WebReadRequest": "knowledge", "WebScraperRequest": "knowledge",
    "BrowserAutomationRequest": "knowledge", "AiCapabilitiesRequest": "knowledge",
    # workspace
    "StorageFileReadRequest": "workspace", "StorageFileWriteRequest": "workspace",
    "OcrRequest": "workspace", "STTRequest": "workspace", "CodeSearchRequest": "workspace",
    "GhRequest": "workspace", "GitOperationRequest": "workspace", "ImageEditRequest": "workspace",
    "RavenMissionRequest": "workspace",
    "MissionObjectivesRequest": "workspace", "MissionCompleteRequest": "workspace",
    # system
    "DockerLogsRequest": "system", "DockerComposeRequest": "system", "DeploymentRequest": "system",
    "CapabilityIndexRequest": "system", "VolumeInventoryRequest": "system",
    "SystemLearningRequest": "system", "RedisInspectRequest": "system",
    "DiscoverySyncRequest": "system", "LLMInfoRequest": "system", "HAConfigRequest": "system",
    "ExecutionLogRequest": "system", "StorageIndexRequest": "system",
    "NetworkDeviceScanRequest": "system", "ControlPlaneRequest": "system",
}
_PREFIX_AREAS = (("Workspace", "workspace"), ("Studio", "workspace"), ("Stl", "workspace"))

#: Longest one-line purpose, so the map stays an index and not a second guide.
PURPOSE_MAX_CHARS = 72

MAP_HEADER = (
    "## Knowledge map\n"
    "Nothing listed here is in this conversation until you fetch it. When a question needs "
    "information from one of these sources, call its tool first and answer from what it returns. "
    "Never guess, and never say you do not know before looking. You can fetch the same "
    "information again later in the conversation whenever you need it."
)


def area_of(tool: str) -> str | None:
    """The area a tool is listed under, or None when nobody assigned one."""
    if tool in TOOL_AREAS:
        return TOOL_AREAS[tool]
    for prefix, area in _PREFIX_AREAS:
        if tool.startswith(prefix):
            return area
    return None


def short_purpose(description: str, limit: int = PURPOSE_MAX_CHARS) -> str:
    """The first sentence of a registry description, cut at a word near ``limit``."""
    text = " ".join((description or "").split())
    match = re.search(r"(?<=[.!?])\s", text)
    first = text[: match.start()] if match else text
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].rstrip(",;:(")
    return f"{cut}..."


def single_turn_tools() -> dict[str, str]:
    """``{tool name: description}`` for every tool the single-turn executor runs."""
    tools: dict[str, str] = {}
    for name, _svc, method, path, _ws, desc, _hint in tr._RAVEN_TOOL_TABLE:
        if name in tr.SINGLE_TURN_EXCLUDED and name != "ControlPlaneRequest":
            continue
        if name != "ControlPlaneRequest" and (method != "POST" or "{" in path):
            continue
        tools[name] = desc
    tools.update(tr.SINGLE_TURN_ONLY_DESCRIPTIONS)
    tools.update(tr.SINGLE_TURN_GARDEN_TOOLS)
    return tools


def raven_tools() -> dict[str, str]:
    """``{tool name: description}`` for every tool Raven's agent loop dispatches."""
    return {name: desc for name, _svc, _m, _p, _ws, desc, _hint in tr._RAVEN_TOOL_TABLE}


def _listed(tools: dict[str, str], *, is_admin: bool) -> dict[str, str]:
    return {
        name: desc
        for name, desc in tools.items()
        if name not in tr.MAP_INTERNAL
        and (name not in tr.HUB_TOOLS or tr.hub_tools_offered())
        and (is_admin or name not in tr.ADMIN_TOOLS)
    }


def workspace_only_tools() -> frozenset[str]:
    """Tools that act on an open workspace (the registry's requires_workspace rows)."""
    return frozenset(name for name, _s, _m, _p, ws, _d, _h in tr._RAVEN_TOOL_TABLE if ws)


def build_knowledge_map(
    tools: dict[str, str], *, is_admin: bool, documented: Iterable[str] = (),
    names_only: Iterable[str] = (),
) -> str:
    """The map for one surface (``tools``), role and configuration.

    ``documented`` names tools the surrounding prompt already describes in full, and
    ``names_only`` tools described elsewhere when they apply (a workspace turn lists its
    workspace tools in full): both are named, not described, so the map stays short.
    """
    listed = _listed(tools, is_admin=is_admin)
    documented_set = {d.lower() for d in documented}
    workspace_set = {n.lower() for n in names_only} - documented_set
    lines = [MAP_HEADER, "", "### Where information lives"]
    searched: list[tuple[str, str]] = []
    for src in tr.knowledge_sources(is_admin=is_admin, offered=set(listed)):
        coll = re.search(r'"collection_name": "([a-z_]+)"', src.example)
        if src.tool == "ContextSearchRequest" and coll:
            searched.append((coll.group(1), src.holds))
        else:
            lines.append(f"- {src.holds}: {src.example}")
    if searched:
        # One call shape, many collections: named once, so the map stays short.
        lines.append(
            '- indexed knowledge: {"tool": "ContextSearchRequest", "collection_name": "<one below>", "query": "..."}'
        )
        lines.extend(f"  - {coll}: {holds}" for coll, holds in searched)
    lines += ["", "### Every tool, by area"]
    by_area: dict[str, list[str]] = {}
    for name in sorted(listed):
        by_area.setdefault(area_of(name) or "other", []).append(name)
    for area, title in (*AREAS, ("other", "Other")):
        names = by_area.get(area)
        if not names:
            continue
        lines.append(f"{title}:")
        described = [n for n in names if n.lower() in documented_set]
        in_workspace = [n for n in names if n.lower() in workspace_set]
        for name in names:
            if name.lower() not in documented_set and name.lower() not in workspace_set:
                lines.append(f"- {name}: {short_purpose(listed[name])}")
        if described:
            lines.append(f"- described above: {', '.join(described)}")
        if in_workspace:
            lines.append(f"- in an open workspace (described there): {', '.join(in_workspace)}")
    return "\n".join(lines)


@lru_cache(maxsize=8)
def _cached_map(surface: str, is_admin: bool, hub: bool, documented: frozenset[str]) -> str:
    if surface == "single_turn":
        # A chat turn in a workspace is given its workspace tools in full (main._workspace_context).
        return build_knowledge_map(single_turn_tools(), is_admin=is_admin, documented=documented,
                                   names_only=workspace_only_tools())
    return build_knowledge_map(raven_tools(), is_admin=is_admin, documented=documented)


def knowledge_map_for(surface: str, *, is_admin: bool, guide: str = "") -> str:
    """The map for ``surface`` (``single_turn`` or ``raven``), skipping what ``guide`` documents.

    Cached per (surface, role, hub, documented set): the inputs are fixed for a given
    configuration, so the text is too.
    """
    if surface not in ("single_turn", "raven"):
        raise ValueError(f"unknown knowledge-map surface {surface!r}")
    headings = re.findall(r"(?m)^###? .*$", guide or "")
    documented = frozenset(
        n.lower() for h in headings for n in re.findall(r"\b([A-Za-z]+Request|garden_[a-z_]+)\b", h)
    )
    return _cached_map(surface, bool(is_admin), tr.hub_tools_offered(), documented)
