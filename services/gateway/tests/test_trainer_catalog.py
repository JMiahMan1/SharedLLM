"""The feature catalogue must come from the code, not from a list somebody edits.

These tests pin the three sources -- route decorators parsed with ``ast``, the
live gateway app (which defers included routers), and the Raven tool table -- and
the derived use-case sentence the curriculum builds its prompts from. If a future
change stops deriving a whole service, the catalogue silently shrinks and the
trainer stops teaching those capabilities, so the counts and the specific known
routes are asserted rather than just "not empty".
"""
from __future__ import annotations

import pathlib

from services.gateway.main import app
from services.gateway.trainer import catalog


def _paths(cat: dict, service: str | None = None) -> set[str]:
    return {
        feature["path"]
        for feature in cat["features"]
        if service is None or feature["service"] == service
    }


def test_every_service_file_yields_its_routes():
    cat = catalog.build_catalog()

    assert "/files/write" in _paths(cat, "workspace_runtime")
    assert "/rag/dream" in _paths(cat, "rag")
    assert "/execute/image_edit" in _paths(cat, "execution")
    assert "/passages" in _paths(cat, "bible")
    # The gateway itself is parsed from source when no live app is passed.
    assert "/api/workspaces/{workspace_id}/ask" in _paths(cat, "gateway")


def test_the_live_app_is_walked_through_deferred_router_includes():
    """FastAPI defers ``include_router``; the catalogue must still see them."""
    cat = catalog.build_catalog(app)

    gateway_paths = _paths(cat, "gateway")
    assert "/api/trainer/catalog" in gateway_paths, "the trainer's own router is missing"
    assert any(path.startswith("/api/garden/") for path in gateway_paths), (
        "garden's included router is missing: deferred includes are not being expanded"
    )
    assert len(gateway_paths) > 400


def test_every_feature_has_a_stable_id_and_a_use_case():
    cat = catalog.build_catalog(app)

    ids = [feature["id"] for feature in cat["features"] if feature["kind"] == "route"]
    assert len(ids) == len(set(ids)), "route feature ids must be unique"
    for feature in cat["features"]:
        assert feature["id"], feature
        assert feature["use_case"], f"{feature['id']} has no use case"
        assert len(feature["use_case"]) > 3


def test_use_cases_read_like_a_person_typed_them():
    assert catalog.use_case_for("POST", "/api/workspaces/{workspace_id}/files/write") == (
        "Ask Jarvis to workspace files write"
    )
    assert catalog.use_case_for("GET", "/api/bible/passages") == "Show bible passages"
    assert catalog.use_case_for("DELETE", "/api/users/me") == "Ask Jarvis to remove user me"


def test_tags_are_derived_from_the_path():
    assert "workspaces" in catalog.tags_for("/files/write")
    assert "memory" in catalog.tags_for("/rag/dream")
    assert "bible" in catalog.tags_for("/passages")
    assert catalog.tags_for("/nothing-in-particular") == ()


def test_tool_features_match_the_tool_table_exactly():
    from services.gateway.tool_registry import _RAVEN_TOOL_TABLE

    tools = {feature["name"] for feature in catalog.build_catalog()["features"] if feature["kind"] == "tool"}
    expected = {str(row[0]) for row in _RAVEN_TOOL_TABLE}

    assert tools == expected
    assert "BibleRequest" in tools


def test_ui_pages_come_from_the_router_table():
    cat = catalog.build_catalog()
    paths = _paths(cat, "ui")

    assert "/workspaces" in paths
    assert "/bible" in paths
    assert "*" not in paths


def test_parse_routes_reads_decorators_and_docstrings(tmp_path: pathlib.Path):
    module = tmp_path / "svc.py"
    module.write_text(
        'from fastapi import FastAPI\n'
        'app = FastAPI()\n'
        '\n'
        '@app.post("/files/write")\n'
        'async def write_file() -> None:\n'
        '    """Write one workspace file."""\n'
        '\n'
        '@app.get("/health")\n'
        'def health() -> None:\n'
        '    pass\n'
        '\n'
        'def not_a_route() -> None:\n'
        '    """Not decorated."""\n',
        encoding="utf-8",
    )

    routes = catalog.parse_routes(module)

    assert (("POST", "/files/write", "write_file", "Write one workspace file")) in routes
    assert (("GET", "/health", "health", "")) in routes
    assert [route[2] for route in routes] == ["write_file", "health"]
