"""A lesson id is namespaced by its author, because rag_items is keyed by id alone.

The handler derived ``lesson-<sha1(rule)>`` from the rule text only. Two users
writing the same rule therefore collide on one primary key and ``INSERT OR
REPLACE`` re-owns the row to whoever ingested last -- the first user's lesson
silently disappears from their own retrieval (visibility is
``user_id IN (?, 'default')``). Convergence for a single author must survive:
the same user re-learning the same rule still produces the same id.
"""

from services.execution.handlers import learning
from services.execution.schemas import SystemLearningRequest, UserContext


class _Resp:
    status = 200

    async def text(self):
        return "{}"

    async def json(self):
        return {}


class _Ctx:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, posts):
        self.posts = posts

    def post(self, url, json=None, headers=None, params=None, timeout=None):
        self.posts.append({"url": url, "json": json})
        return _Ctx(_Resp())


def _capture(monkeypatch):
    posts = []
    client = _Client(posts)
    monkeypatch.setattr(
        learning.aiohttp, "ClientSession", lambda **kwargs: _Ctx(client)
    )
    return posts


async def _ingest(monkeypatch, user, rule):
    posts = _capture(monkeypatch)
    req = SystemLearningRequest(
        user_context=UserContext(user=user), rule=rule, topic="t", content="c"
    )
    result = await learning.handle_system_learning(req)
    assert result.status == "SUCCESS", result.message
    return posts[0]["json"]["metadata"]["id"]


async def test_the_same_rule_written_by_two_users_gets_two_ids(monkeypatch):
    alice = await _ingest(monkeypatch, "alice", "when x do y")
    bob = await _ingest(monkeypatch, "bob", "when x do y")
    assert alice != bob
    assert alice.startswith("lesson-")
    assert bob.startswith("lesson-")


async def test_the_same_user_relearning_the_same_rule_converges(monkeypatch):
    first = await _ingest(monkeypatch, "alice", "when x do y")
    second = await _ingest(monkeypatch, "alice", "when x do y")
    assert first == second


async def test_different_rules_still_get_different_ids(monkeypatch):
    first = await _ingest(monkeypatch, "alice", "when x do y")
    other = await _ingest(monkeypatch, "alice", "when z do w")
    assert first != other


async def test_the_ingest_payload_owns_the_row_it_names(monkeypatch):
    posts = _capture(monkeypatch)
    req = SystemLearningRequest(
        user_context=UserContext(user="alice"), rule="when x do y", topic="t", content="c"
    )
    result = await learning.handle_system_learning(req)
    assert result.status == "SUCCESS"
    payload = posts[0]["json"]
    assert payload["user_id"] == "alice"
    assert payload["metadata"]["id"].startswith("lesson-")
    import json
    assert json.loads(payload["content"])["id"] == payload["metadata"]["id"]
