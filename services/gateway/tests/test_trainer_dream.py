"""Dreaming must produce a to-do list, not just a report.

The proposal rules are what turn "the memory was compacted" into "rewrite this
lesson" or "document this tool", and they are the only reason a cycle changes
anything. These tests pin each rule, the merge (highest priority wins), and the
lesson shape written back into RAG -- which must match the execution learning
handler's compact JSON so the mission prompt and the next dream treat it as an
ordinary lesson.
"""
from __future__ import annotations

import json

import pytest

from services.gateway.trainer import dream


def test_a_failed_dream_proposes_nothing():
    assert dream.proposals_from_dream(None) == []
    assert dream.proposals_from_dream({"status": "ERROR"}) == []


def test_degraded_lessons_become_rewrite_proposals():
    report = {"status": "SUCCESS", "reviewed": 9, "degraded": ["lesson-a"]}

    proposals = dream.proposals_from_dream(report)

    assert proposals[0]["kind"] == "lesson_rewrite"
    assert "lesson-a" in proposals[0]["title"]
    assert proposals[0]["priority"] == 9


def test_an_applied_but_unvalidated_lesson_becomes_a_validation_proposal():
    report = {
        "status": "SUCCESS",
        "reviewed": 9,
        "validation_checklist": [{"id": "lesson-b", "rule": "restart caddy", "applied_count": 3}],
    }

    kinds = [proposal["kind"] for proposal in dream.proposals_from_dream(report)]

    assert "lesson_validation" in kinds


def test_a_thin_memory_asks_for_seeding():
    proposals = dream.proposals_from_dream({"status": "SUCCESS", "reviewed": 2})

    assert any(proposal["kind"] == "lesson_seed" for proposal in proposals)


def test_compaction_asks_for_shorter_lessons():
    proposals = dream.proposals_from_dream(
        {"status": "SUCCESS", "reviewed": 9, "compacted": ["lesson-c"]}
    )

    assert any(proposal["kind"] == "curriculum_tighten" for proposal in proposals)


def test_an_unloaded_model_is_the_top_proposal():
    report = {
        "stopped_reason": "model_unavailable",
        "cases": [{"id": "c1", "error": "model not loaded", "passed": False}],
        "by_bucket": {},
    }

    proposals = dream.proposals_from_failures(report)

    assert proposals[0]["kind"] == "model_unavailable"
    assert proposals[0]["priority"] == 10
    assert "model not loaded" in proposals[0]["evidence"]


def test_a_tool_the_model_missed_becomes_a_documentation_task():
    report = {
        "cases": [
            {"id": "c1", "passed": False, "source": "catalog:tool_registry",
             "title": "Choose BibleRequest for the job it is for"},
        ],
        "by_bucket": {"tools": {"cases": 1, "passed": 0, "failed_ids": ["c1"]}},
    }

    proposals = dream.proposals_from_failures(report)
    kinds = {proposal["kind"] for proposal in proposals}

    assert "tool_doc" in kinds
    doc = next(proposal for proposal in proposals if proposal["kind"] == "tool_doc")
    assert "BibleRequest" in doc["title"]
    assert "single_turn_tool_guide.md" in doc["detail"]


def test_failing_buckets_become_curriculum_work():
    report = {
        "cases": [],
        "by_bucket": {
            "repair": {"cases": 3, "passed": 1, "failed_ids": ["a", "b"]},
            "writing": {"cases": 2, "passed": 2, "failed_ids": []},
        },
    }

    kinds = {proposal["kind"] for proposal in dream.proposals_from_failures(report)}

    assert kinds == {"curriculum_repair"}


def test_merging_keeps_the_highest_priority_copy():
    low = {"id": "p1", "kind": "tool_doc", "title": "t", "detail": "d",
           "priority": 2, "evidence": [], "action": "a"}
    high = {**low, "priority": 9}
    other = {**low, "id": "p2", "priority": 5}

    merged = dream.merge_proposals([low, other], [high])

    assert [proposal["id"] for proposal in merged] == ["p1", "p2"]
    assert merged[0]["priority"] == 9


def test_a_lesson_is_the_same_compact_shape_the_handler_writes():
    proposal = {"id": "proposal-x", "kind": "tool_doc", "title": "Teach BibleRequest",
                "detail": "detail", "priority": 8, "evidence": [], "action": "Document it."}

    payload = dream.lesson_payload(proposal, user_id="alice")
    content = json.loads(payload["content"])

    assert payload["collection_name"] == "system_learnings"
    assert payload["metadata"]["type"] == "improvement_proposal"
    assert payload["metadata"]["confidence"] == 0.35
    assert content["id"] == payload["metadata"]["id"]
    assert content["rule"].startswith("When improving Jarvis:")
    assert len(content["summary"]) <= 400
    assert set(content) == {"id", "topic", "rule", "root_cause", "outcome",
                            "confidence", "tags", "summary"}


async def test_ingesting_proposals_is_bounded_and_survives_a_failure(monkeypatch):
    sent: list[dict] = []

    async def fake_request(method, path, *, params=None, body=None, timeout=60.0):
        sent.append(body)
        if len(sent) == 1:
            raise dream.RagUnavailable("RAG /rag/ingest did not answer")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(dream, "request", fake_request)
    proposals = [
        {"id": f"p{i}", "kind": "tool_doc", "title": "t", "detail": "d",
         "priority": 5, "evidence": [], "action": f"a{i}"}
        for i in range(5)
    ]

    written = await dream.ingest_proposals(proposals, user_id="alice", limit=2)

    assert len(sent) == 2, "only the requested number of lessons may be written"
    assert len(written["written"]) == 1, "a failed write is skipped, not fatal"
    assert written["failed"][0]["proposal"] == "p0", "and it is reported, not hidden"


class _FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse, **kwargs):
        self._response = response

    def request(self, *args, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _rag_answers(monkeypatch, status: int, text: str) -> None:
    monkeypatch.setattr(
        dream, "get_client", lambda: _FakeSession(_FakeResponse(status, text))
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_a_rag_4xx_is_a_failure_not_data(monkeypatch, status):
    _rag_answers(monkeypatch, status, '{"detail": "refused"}')

    with pytest.raises(dream.RagRejected, match=str(status)):
        await dream.request("POST", "/rag/ingest", body={})


async def test_a_rag_5xx_is_unavailable(monkeypatch):
    _rag_answers(monkeypatch, 503, "down")

    with pytest.raises(dream.RagUnavailable):
        await dream.request("POST", "/rag/dream")


async def test_a_refused_lesson_write_is_not_counted_as_written(monkeypatch):
    _rag_answers(monkeypatch, 422, '{"detail": "bad lesson"}')
    proposal = {"id": "p1", "kind": "tool_doc", "title": "t", "detail": "d",
                "priority": 5, "evidence": [], "action": "a"}

    result = await dream.ingest_proposals([proposal])

    assert result["written"] == []
    assert "422" in result["failed"][0]["error"]


async def test_infrastructure_proposals_are_never_written(monkeypatch):
    sent: list[dict] = []

    async def fake_request(method, path, *, params=None, body=None, timeout=60.0):
        sent.append(body)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(dream, "request", fake_request)
    proposals = [
        dream.make_proposal(kind, "t", f"d-{kind}", priority=10, evidence=[], action="a")
        for kind in ("hardware_busy", "host_busy", "model_unavailable")
    ] + [dream.make_proposal("tool_doc", "t", "d", priority=1, evidence=[], action="document it")]

    result = await dream.ingest_proposals(proposals, limit=3)

    assert len(sent) == 1 and "tool_doc" in sent[0]["content"]
    assert len(result["written"]) == 1
