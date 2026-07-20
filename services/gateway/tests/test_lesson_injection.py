import pytest


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeClient:
    """Records the POST call and returns a canned RAG search response."""

    def __init__(self, status: int, payload: dict):
        self._status = status
        self._payload = payload
        self.last_json = None

    async def post(self, url, json=None, headers=None, timeout=None):
        self.last_json = json
        return _FakeResp(self._status, self._payload)


def _hit(rule, outcome="success", conf=0.9, content=None):
    return {
        "rule": rule,
        "outcome": outcome,
        "confidence": conf,
        "content": content or rule,
        "score": 0.5,
    }


async def test_fetch_relevant_lessons_formats_compact_lines():
    from services.gateway.main import _fetch_relevant_lessons

    client = _FakeClient(
        200,
        {
            "results": [
                _hit("When provisioning a GitHub repo, create it via gh repo create first.", "success", 0.9),
                _hit("Verify remote tracking before pushing to avoid a detached push.", "partial", 0.8),
            ]
        },
    )
    out = await _fetch_relevant_lessons("write a python lib and push to github", client)
    assert "When provisioning a GitHub repo" in out
    assert "[success, conf 0.90]" in out
    assert "[partial, conf 0.80]" in out
    # relevance query was forwarded to the RAG search endpoint
    assert client.last_json["collection_name"] == "system_learnings"
    assert client.last_json["query"]


async def test_fetch_relevant_lessons_caps_at_limit():
    from services.gateway.main import _fetch_relevant_lessons

    many = [_hit(f"lesson number {i} about doing the thing correctly") for i in range(12)]
    client = _FakeClient(200, {"results": many})
    out = await _fetch_relevant_lessons("do the thing", client, limit=5)
    # only the capped number of bullet lines, never the full store
    assert out.count("\n- ") + (1 if out.startswith("- ") else 0) <= 5
    assert "lesson number 11" not in out


async def test_fetch_relevant_lessons_falls_back_to_content():
    from services.gateway.main import _fetch_relevant_lessons

    client = _FakeClient(200, {"results": [{"content": "Always set the default branch to main.", "outcome": "success"}]})
    out = await _fetch_relevant_lessons("git branch", client)
    assert "Always set the default branch to main." in out


async def test_fetch_relevant_lessons_fails_safe():
    from services.gateway.main import _fetch_relevant_lessons

    # non-200 -> empty (do not block mission start)
    client = _FakeClient(500, {})
    assert await _fetch_relevant_lessons("anything", client) == ""

    # exception -> empty
    class _Boom:
        async def post(self, *a, **k):
            raise RuntimeError("rag down")

    assert await _fetch_relevant_lessons("anything", _Boom()) == ""

    # no hits -> empty
    assert await _fetch_relevant_lessons("anything", _FakeClient(200, {"results": []})) == ""
