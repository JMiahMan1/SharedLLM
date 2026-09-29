"""LastKnownGoodCache: serve the last real value when a dependency is down."""
import pytest

from services.common.last_known_good import LastKnownGoodCache


@pytest.fixture
def cache():
    return LastKnownGoodCache("test")


@pytest.mark.asyncio
async def test_returns_the_fresh_value_and_remembers_it(cache):
    async def load():
        return {"mode": "heat"}

    value, stale = await cache.fetch_or_recall("a", load)
    assert value == {"mode": "heat"}
    assert stale is False
    assert cache.recall("a") == {"mode": "heat"}


@pytest.mark.asyncio
async def test_falls_back_to_the_last_good_value_when_the_fetch_fails(cache):
    async def ok():
        return "v1"

    await cache.fetch_or_recall("a", ok)

    async def boom():
        raise ConnectionError("service down")

    value, stale = await cache.fetch_or_recall("a", boom)
    assert value == "v1"
    assert stale is True


@pytest.mark.asyncio
async def test_reports_a_miss_rather_than_inventing_a_default(cache):
    """No cached value is not the same as an empty one."""

    async def boom():
        raise ConnectionError("service down")

    value, stale = await cache.fetch_or_recall("a", boom)
    assert value is None
    assert stale is False


@pytest.mark.asyncio
async def test_surfaces_every_kind_of_failure_identically(cache):
    for exc in (ConnectionError("x"), ValueError("y"), KeyError("z"), RuntimeError()):
        async def boom(e=exc):
            raise e

        assert await cache.fetch_or_recall("a", boom) == (None, False)


@pytest.mark.asyncio
async def test_a_successful_fetch_replaces_a_stale_entry(cache):
    async def first():
        return "v1"

    async def second():
        return "v2"

    await cache.fetch_or_recall("a", first)
    value, stale = await cache.fetch_or_recall("a", second)
    assert (value, stale) == ("v2", False)
    assert cache.recall("a") == "v2"


@pytest.mark.asyncio
async def test_a_falsy_value_is_still_a_real_value(cache):
    """None/0/[] are legitimate answers; only exceptions degrade."""
    async def empty():
        return []

    value, stale = await cache.fetch_or_recall("a", empty)
    assert value == []
    assert stale is False
    assert cache.recall("a") == []


@pytest.mark.asyncio
async def test_keys_are_independent(cache):
    def loader_for(key):
        async def _load():
            return key.upper()
        return _load

    await cache.fetch_or_recall("a", loader_for("a"))
    await cache.fetch_or_recall("b", loader_for("b"))
    assert cache.recall("a") == "A"
    assert cache.recall("b") == "B"


@pytest.mark.asyncio
async def test_evidence_for_one_user_never_leaks_into_another(cache):
    async def ok():
        return "v1"

    await cache.fetch_or_recall("alice", ok)
    assert cache.recall("bob") is None


def test_evidence_does_not_grow_without_bound():
    cache = LastKnownGoodCache("test", max_entries=10)
    for i in range(100):
        cache.remember(f"k{i}", i)
    assert len(cache) == 10
    # Oldest insertions are the ones dropped.
    assert cache.recall("k99") == 99
    assert cache.recall("k0") is None


def test_forget_clears_one_key_or_everything():
    cache = LastKnownGoodCache("test")
    cache.remember("a", 1)
    cache.remember("b", 2)
    cache.forget("a")
    assert cache.recall("a") is None
    assert cache.recall("b") == 2
    cache.forget()
    assert len(cache) == 0
