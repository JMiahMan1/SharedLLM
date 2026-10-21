from fastapi.testclient import TestClient

from services.bible.tests.conftest import auth


def _create_session(client: TestClient, username: str = "reader") -> int:
    created = client.post(
        "/study/sessions",
        json={"username": username, "reference": "John 3:16", "version": "kjv", "title": "John 3:16"},
        headers=auth(),
    )
    assert created.status_code == 200, created.text
    return created.json()["session"]["id"]


def test_study_memory_finds_saved_questions_and_answers(loaded_client: TestClient):
    session_id = _create_session(loaded_client)
    added = loaded_client.post(
        f"/study/sessions/{session_id}/turn",
        params={"username": "reader"},
        json={
            "question": "Why did Jesus speak about being born again?",
            "answer": "Nicodemus needed a spiritual birth, not a second physical one.",
            "references": ["John 3:3"],
        },
        headers=auth(),
    )
    assert added.status_code == 200, added.text

    found = loaded_client.get(
        "/study/sessions/search",
        params={"username": "reader", "q": "born again"},
        headers=auth(),
    )
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["count"] == 1
    assert body["results"][0]["session_id"] == session_id
    assert body["results"][0]["references"] == ["John 3:3"]
    assert "Nicodemus" in body["results"][0]["answer"]


def test_study_memory_is_scoped_to_the_reader(loaded_client: TestClient):
    _create_session(loaded_client, "reader")
    other = loaded_client.get(
        "/study/sessions/search",
        params={"username": "someone-else", "q": "John"},
        headers=auth(),
    )
    assert other.status_code == 200
    assert other.json()["results"] == []
