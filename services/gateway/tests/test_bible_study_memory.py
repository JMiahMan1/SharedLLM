"""Study memory: plan reflections and promoted notes go into RAG with citable ids."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def wired(monkeypatch):
    calls: list[tuple] = []
    remembered: list[tuple] = []
    answers: dict = {}

    async def _caller(_request):
        return "sam"

    async def _bible(method, path, *, label, params=None, json=None):
        calls.append((method, path, params, json))
        return answers.get((method, path), {})

    async def _ingest(username, memory_id, kind, content, extra=None):
        remembered.append((username, memory_id, kind, content))
        return answers.get("rag_ok", True)

    monkeypatch.setattr(gateway_main, "_bible_caller", _caller)
    monkeypatch.setattr(gateway_main, "_bible_json", _bible)
    monkeypatch.setattr(gateway_main, "_ingest_study_memory", _ingest)
    return TestClient(app), calls, remembered, answers


def test_a_reflection_is_kept_and_remembered(wired):
    client, calls, remembered, answers = wired
    answers[("PUT", "/plans/romans-16/days/1/reflection")] = {
        "plan_name": "Romans in 16 days", "day": 1, "readings": ["Romans 1"], "text": "Not ashamed of the gospel."}
    body = client.put("/api/bible/plans/romans-16/days/1/reflection", json={"text": "Not ashamed of the gospel."}).json()
    assert body["remembered"] is True
    assert calls[0][2] == {"username": "sam"}
    [(user, memory_id, kind, content)] = remembered
    assert (user, memory_id, kind) == ("sam", "bible-plan-romans-16-1-sam", "plan_reflection")
    assert "Romans 1" in content and "Not ashamed" in content


def test_only_your_own_note_is_promoted(wired):
    client, _, remembered, answers = wired
    answers[("GET", "/marks")] = {"marks": [
        {"id": 7, "kind": "note", "osis": "John", "chapter": 3, "verse_start": 16},
        {"id": 8, "kind": "highlight", "osis": "John", "chapter": 3, "verse_start": 17},
    ]}
    ok = client.post("/api/bible/marks/7/remember", json={"text": "God's love is for the world.", "reference": "John 3:16"})
    assert ok.status_code == 200 and ok.json()["id"] == "bible-note-7"
    assert remembered[0][1] == "bible-note-7" and "John 3:16" in remembered[0][3]
    assert client.post("/api/bible/marks/8/remember", json={"text": "x"}).status_code == 404  # not a note
    assert client.post("/api/bible/marks/99/remember", json={"text": "x"}).status_code == 404  # not yours
    assert client.post("/api/bible/marks/7/remember", json={"text": ""}).status_code == 422


def test_rag_down_is_said_plainly(wired):
    client, _, _, answers = wired
    answers[("GET", "/marks")] = {"marks": [{"id": 7, "kind": "note"}]}
    answers["rag_ok"] = False
    assert client.post("/api/bible/marks/7/remember", json={"text": "x"}).status_code == 503
