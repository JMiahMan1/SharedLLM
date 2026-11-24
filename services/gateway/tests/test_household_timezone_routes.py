"""The admin's household time zone control (docs/CONFIG.md, Time zone)."""
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()


@pytest.fixture
def gw(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "_require_admin", AsyncMock(return_value="alex"))
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    return main


async def test_status_reports_the_setting_as_the_source(gw, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setattr(gw, "get_all_settings", AsyncMock(return_value={"timezone": "America/Denver"}))
    status = await gw.get_household_timezone(MagicMock())
    assert status["zone"] == "America/Denver"
    assert status["source"] == "household setting"
    assert status["env_tz"] == "Europe/Berlin"
    assert status["error"] is None


async def test_status_falls_to_tz_and_says_so(gw, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    monkeypatch.setattr(gw, "get_all_settings", AsyncMock(return_value={"timezone": ""}))
    status = await gw.get_household_timezone(MagicMock())
    assert (status["zone"], status["source"]) == ("Europe/Berlin", "TZ in .env")


async def test_status_with_nothing_set_carries_the_error(gw, monkeypatch):
    monkeypatch.setattr(gw, "get_all_settings", AsyncMock(return_value={}))
    status = await gw.get_household_timezone(MagicMock())
    assert status["zone"] is None
    assert "Settings > Household time zone" in status["error"]


async def test_put_rejects_a_zone_that_is_not_iana(gw):
    request = MagicMock()
    request.json = AsyncMock(return_value={"timezone": "Mars/Base"})
    with pytest.raises(HTTPException) as err:
        await gw.put_household_timezone(request)
    assert err.value.status_code == 400


async def test_put_writes_both_settings_through_identity(gw, monkeypatch):
    seen = []

    class _Resp:
        status = 200

        async def text(self):
            return ""

    class _Client:
        async def patch(self, url, json=None, headers=None, timeout=None):
            seen.append((url.rsplit("/", 1)[-1], json))
            return _Resp()

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(gw, "shared_http_client", fake_client)
    monkeypatch.setattr(
        gw, "get_all_settings",
        AsyncMock(return_value={"timezone": "America/Denver", "timezone_from_location": "true"}),
    )
    request = MagicMock()
    request.headers = {"Authorization": "Bearer t"}
    request.json = AsyncMock(return_value={"timezone": "America/Denver", "from_location": True})
    status = await gw.put_household_timezone(request)
    assert seen == [("timezone", {"value": "America/Denver"}), ("timezone_from_location", {"value": "true"})]
    assert status["zone"] == "America/Denver" and status["from_location"] is True


async def test_the_time_answer_names_how_to_set_a_missing_zone(gw, monkeypatch):
    monkeypatch.setattr("services.gateway.orchestrator.get_all_settings", AsyncMock(return_value={}))
    answer = await gw.build_time_or_date_response("what time is it")
    assert "Household time zone" in answer and "TZ" in answer


def test_the_time_answer_is_said_the_way_a_person_says_it(gw):
    from datetime import datetime
    evening = datetime(2026, 11, 23, 19, 29)
    assert gw.spoken_time_answer("what time is it", evening) == "It's 7:29 PM."
    assert gw.spoken_time_answer("what's the date today", evening) == "Today is Monday, November 23rd."
    assert gw.spoken_time_answer("what time and day is it", evening) == "It's 7:29 PM on Monday, November 23rd."
    assert gw.spoken_time_answer("what time is it", datetime(2026, 11, 1, 0, 5)) == "It's 12:05 AM."
    assert gw.spoken_time_answer("what time is it", datetime(2026, 11, 1, 12, 0)) == "It's 12:00 PM."


def test_ordinal_days():
    from services.gateway.main import _ordinal
    assert [_ordinal(d) for d in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st"]
