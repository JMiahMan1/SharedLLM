"""A mission's time limit is enforced from outside the loop too."""
from services.gateway import agent_loop, background_worker


def test_the_limit_comes_from_settings_and_zero_means_none():
    assert agent_loop.mission_time_limit({"raven_max_total_seconds": "900"}) == 900
    assert agent_loop.mission_time_limit({"raven_max_total_seconds": "0"}) == float("inf")
    assert agent_loop.mission_time_limit({"raven_max_total_seconds": "abc"}) == agent_loop.RAVEN_MAX_TOTAL_SECONDS
    assert agent_loop.mission_time_limit(None) == agent_loop.RAVEN_MAX_TOTAL_SECONDS


async def test_the_worker_allows_the_limit_plus_a_grace_period(monkeypatch):
    async def settings():
        return {"raven_max_total_seconds": "1800"}

    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", settings)
    assert await background_worker._mission_deadline() == 1800 + background_worker.MISSION_DEADLINE_GRACE_S


async def test_no_cap_and_unreadable_settings_mean_no_deadline(monkeypatch):
    async def no_cap():
        return {"raven_max_total_seconds": "0"}

    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", no_cap)
    assert await background_worker._mission_deadline() is None

    async def broken():
        raise RuntimeError("identity down")

    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", broken)
    assert await background_worker._mission_deadline() is None
