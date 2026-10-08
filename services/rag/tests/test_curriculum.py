"""Tests for the protocol curriculum: seeding idempotency, learning tag
filter, toolchain inventory sync, and resource inventory endpoints."""

import json
import time


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
async def test_sync_capabilities_prunes_stale_binaries(tmp_path):
    _install_fakes(tmp_path)
    first = {
        "capabilities": [
            {
                "name": "pandoc",
                "type": "binary",
                "version": "3.1.11",
                "tags": ["typesetting"],
                "description": "Universal document converter",
            },
            {
                "name": "tshark",
                "type": "binary",
                "version": "4.4.16",
                "tags": ["network"],
                "description": "Packet capture",
            },
        ]
    }
    await rag_main.sync_capabilities(first)
    assert (await rag_main.get_toolchain())["count"] == 2

    second = {
        "capabilities": [
            {
                "name": "pandoc",
                "type": "binary",
                "version": "3.1.11",
                "tags": ["typesetting"],
                "description": "Universal document converter",
            }
        ]
    }
    await rag_main.sync_capabilities(second)
    toolchain = await rag_main.get_toolchain()
    assert toolchain["count"] == 1
    assert toolchain["tools"][0]["name"] == "pandoc"


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


@pytest.mark.asyncio
async def test_priority_score_with_benchmark_boost(tmp_path):
    _install_fakes(tmp_path)
    from services.rag.main import _lesson_priority_score
    base = {"applied_count": 2, "usage_count": 3, "confidence": 0.7, "created_at": time.time()}
    base_score = _lesson_priority_score(base)
    with_bench = dict(base)
    with_bench["metadata"] = {"benchmark_score": 85}
    bench_score = _lesson_priority_score(with_bench)
    assert bench_score > base_score, f"benchmark should boost score: {bench_score} <= {base_score}"
    no_bench = dict(base)
    no_bench["metadata"] = {"benchmark_score": 0}
    no_score = _lesson_priority_score(no_bench)
    assert no_score == base_score or abs(no_score - base_score) < 0.01


def _benchmarks_fixture(tmp_path):
    """A benchmark_tests.json stand-in.

    The default BENCHMARKS_PATH points into the separate `alpaca` repo, which is
    not present in CI, so tests must not rely on a developer's checkout.
    """
    fixture = tmp_path / "benchmark_tests.json"
    fixture.write_text(json.dumps({
        "coding": [
            {"id": "code-1", "label": "Write a function", "expected": "def f(): ..."},
            {"id": "code-2", "label": "Fix the failing test"},
        ],
        "reasoning": [
            {"id": "reason-1", "label": "Deduce the ordering"},
        ],
    }))
    return fixture


@pytest.mark.asyncio
async def test_ingest_benchmark_curriculum(tmp_path):
    _install_fakes(tmp_path)
    fixture = _benchmarks_fixture(tmp_path)
    resp = await rag_main.ingest_benchmark_curriculum(path=str(fixture))
    assert resp["status"] == "SUCCESS"
    assert resp["categories_ingested"] == 2
    assert set(resp["lesson_ids"]) == {"benchmark-coding", "benchmark-reasoning"}
    items = (await rag_main.list_learnings())["items"]
    benchmark_items = [i for i in items if i["metadata"].get("type") == "benchmark_curriculum"]
    assert len(benchmark_items) == resp["categories_ingested"]
    for item in benchmark_items:
        meta = item["metadata"]
        assert meta.get("id", "").startswith("benchmark-")
        assert meta.get("ground_truth_count", 0) >= 0
    # The fixture carries one expected answer, so the count must be real rather
    # than a defaulting zero.
    by_id = {i["metadata"]["id"]: i["metadata"] for i in benchmark_items}
    assert by_id["benchmark-coding"]["ground_truth_count"] == 1
    assert by_id["benchmark-reasoning"]["ground_truth_count"] == 0


@pytest.mark.asyncio
async def test_benchmark_insights(tmp_path):
    _install_fakes(tmp_path)
    await rag_main.ingest_benchmark_curriculum(path=str(_benchmarks_fixture(tmp_path)))
    insights = await rag_main.benchmark_insights()
    assert insights["status"] == "SUCCESS"
    assert "proven_capabilities" in insights
    assert "priority_gaps" in insights
    assert "guidance" in insights


def test_the_library_protocol_lesson_teaches_calibre_before_the_web():
    """Raven's mission prompt must know the shelf exists and prefer it.

    The 2026-10 Macbeth incident: a book question answered from the web while
    the household's own library held the answer. The lesson is pinned into
    every mission, so it is the lever that stops the repeat.
    """
    lesson = next(
        (l for l in rag_main._PROTOCOL_LESSONS if l["id"] == "lesson-proto-library"),
        None,
    )
    assert lesson is not None, "lesson-proto-library is missing from the protocol curriculum"
    assert "protocol" in lesson["tags"]
    assert "calibre_files" in lesson["rule"]
    assert "CalibreRequest" in lesson["rule"]
    assert "WebSearchRequest" in lesson["rule"], "the lesson must say when the web is the fallback"
    assert lesson["rule"].index("FIRST") < lesson["rule"].index("WebSearchRequest")


def test_the_scripture_protocol_lesson_points_at_bible_request():
    """Raven's mission prompt must know scripture comes from the Bible service.

    Scripture text is not in RAG (there is no bible collection), so the lesson
    is the only thing that stops a mission quoting a paraphrase from the web
    when the corpus holds the verse.
    """
    lesson = next(
        (l for l in rag_main._PROTOCOL_LESSONS if l["id"] == "lesson-proto-scripture"),
        None,
    )
    assert lesson is not None, "lesson-proto-scripture is missing from the protocol curriculum"
    assert "protocol" in lesson["tags"]
    assert "scripture" in lesson["tags"] and "bible" in lesson["tags"]
    assert "BibleRequest" in lesson["rule"]
    assert "study_notes" in lesson["rule"], "commentary questions must route to study notes"
    assert "catalogue" in lesson["rule"], "installed translations must be discoverable"
    assert "{reference} ({version})" in lesson["rule"], "citations must name reference and version"
    assert "WebSearchRequest" in lesson["rule"], "the lesson must forbid quoting scripture from the web"
    assert lesson["rule"].index("FIRST") < lesson["rule"].index("WebSearchRequest")
