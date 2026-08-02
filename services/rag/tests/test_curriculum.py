"""Tests for the protocol curriculum: seeding idempotency, learning tag
filter, toolchain inventory sync, and resource inventory endpoints."""

import json
import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.schemas import IngestRequest
from services.rag.store import NumpyVecAdapter

DIM = 8


def _install_fakes(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()

    def fake_embed(texts):
        out = []
        for text in texts:
            v = [0.0] * DIM
            for i, ch in enumerate(text):
                v[i % DIM] += ord(ch)
            out.append(v)
        return out

    rag_main.embed = fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


async def _ingest(
    collection: str,
    content: str,
    metadata: dict,
    user_id: str = "default",
) -> str:
    req = IngestRequest(
        user_id=user_id,
        content=content,
        collection_name=collection,
        metadata=metadata,
    )
    resp = await rag_main.ingest(req)
    return resp["id"]


def _lesson_meta(rule: str, tags: list[str], confidence: float = 0.5) -> dict:
    return {
        "topic": "Raven lesson: test",
        "rule": rule,
        "root_cause": "rc",
        "outcome": "success",
        "confidence": confidence,
        "tags": tags,
        "type": "learning",
        "supersedes": [],
    }


@pytest.mark.asyncio
async def test_seed_protocol_lessons_is_idempotent(tmp_path):
    _install_fakes(tmp_path)

    created = rag_main._seed_protocol_lessons()
    assert created == len(rag_main._PROTOCOL_LESSONS)

    items = (await rag_main.list_learnings(tag="protocol"))["items"]
    ids = {i["id"] for i in items}
    assert ids == {lesson["id"] for lesson in rag_main._PROTOCOL_LESSONS}
    assert all(lesson["id"].startswith("lesson-proto-") for lesson in rag_main._PROTOCOL_LESSONS)
    for item in items:
        assert "protocol" in item["metadata"]["tags"]

    again = rag_main._seed_protocol_lessons()
    assert again == 0
    assert (await rag_main.list_learnings(tag="protocol"))["count"] == len(
        rag_main._PROTOCOL_LESSONS
    )


@pytest.mark.asyncio
async def test_seed_protocol_lessons_uses_stable_ids(tmp_path):
    _install_fakes(tmp_path)
    rag_main._seed_protocol_lessons()

    items = (await rag_main.list_learnings(tag="protocol"))["items"]
    assert len({i["id"] for i in items}) == len(rag_main._PROTOCOL_LESSONS)
    for lesson in rag_main._PROTOCOL_LESSONS:
        assert rag_main._item_exists(lesson["id"])


@pytest.mark.asyncio
async def test_learning_tag_filter(tmp_path):
    _install_fakes(tmp_path)
    await _ingest(
        "system_learnings",
        json.dumps({"id": "x", "rule": "Alpha rule"}),
        _lesson_meta("Alpha rule", ["alpha", "protocol"]),
    )
    await _ingest(
        "system_learnings",
        json.dumps({"id": "y", "rule": "Beta rule"}),
        _lesson_meta("Beta rule", ["beta"]),
    )

    alpha = (await rag_main.list_learnings(tag="alpha"))["items"]
    beta = (await rag_main.list_learnings(tag="beta"))["items"]
    none = (await rag_main.list_learnings(tag="missing"))["items"]

    assert {i["metadata"]["rule"] for i in alpha} == {"Alpha rule"}
    assert {i["metadata"]["rule"] for i in beta} == {"Beta rule"}
    assert none == []


@pytest.mark.asyncio
async def test_toolchain_endpoint_returns_only_binaries(tmp_path):
    _install_fakes(tmp_path)
    resp = await rag_main.sync_capabilities(
        {
            "capabilities": [
                {
                    "name": "pandoc",
                    "type": "binary",
                    "version": "3.1.11",
                    "tags": ["typesetting", "document"],
                    "description": "Universal document converter",
                },
                {
                    "name": "magick",
                    "type": "binary",
                    "description": "ImageMagick 7 CLI",
                },
                {
                    "name": "sharedllm_git",
                    "type": "tool",
                    "schema": "GitOperationRequest",
                },
            ]
        }
    )
    assert resp["status"] == "SUCCESS"
    assert resp["count"] == 3

    toolchain = await rag_main.get_toolchain()
    assert toolchain["status"] == "SUCCESS"
    assert toolchain["count"] == 2
    tools = {t["name"]: t for t in toolchain["tools"]}
    assert tools["pandoc"]["version"] == "3.1.11"
    assert tools["pandoc"]["tags"] == ["typesetting", "document"]
    assert tools["magick"]["version"] == ""
    assert "sharedllm_git" not in tools


@pytest.mark.asyncio
async def test_sync_capabilities_truncates_long_version(tmp_path):
    _install_fakes(tmp_path)
    long_version = "v" * 80
    await rag_main.sync_capabilities(
        {
            "capabilities": [
                {
                    "name": "ffmpeg",
                    "type": "binary",
                    "version": long_version,
                    "tags": ["media"],
                    "description": "Audio/video",
                }
            ]
        }
    )
    toolchain = await rag_main.get_toolchain()
    assert toolchain["tools"][0]["version"] == "v" * 60


@pytest.mark.asyncio
async def test_nextcloud_resources_inventory(tmp_path):
    _install_fakes(tmp_path)
    await _ingest(
        "nextcloud_files",
        "some content",
        {"path": "/Documents/report.pdf", "friendly_name": "Quarterly Report"},
    )
    await _ingest(
        "nextcloud_files",
        "more content",
        {"path": "/Photos/party.jpg"},
    )

    result = await rag_main.list_nextcloud_resources()
    assert result["status"] == "SUCCESS"
    assert result["count"] == 2
    files = {f["name"]: f for f in result["files"]}
    assert files["Quarterly Report"]["path"] == "/Documents/report.pdf"
    assert files["party.jpg"]["path"] == "/Photos/party.jpg"
    assert all(f["indexed_at"] for f in result["files"])


@pytest.mark.asyncio
async def test_ha_resources_inventory(tmp_path):
    _install_fakes(tmp_path)
    await _ingest(
        "ha_entities",
        "entity snapshot",
        {
            "entity_id": "sensor.office_temperature",
            "friendly_name": "Office Temperature",
            "state": "21.5",
        },
    )

    result = await rag_main.list_ha_resources()
    assert result["status"] == "SUCCESS"
    assert result["count"] == 1
    entity = result["entities"][0]
    assert entity["entity_id"] == "sensor.office_temperature"
    assert entity["friendly_name"] == "Office Temperature"
    assert entity["state"] == "21.5"
