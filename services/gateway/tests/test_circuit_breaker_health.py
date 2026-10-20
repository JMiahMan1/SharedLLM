from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.common.circuit_breaker import reset_breakers
from services.gateway import main as gateway_main


async def test_circuit_breaker_health_is_admin_only(monkeypatch):
    reset_breakers()
    monkeypatch.setattr(
        gateway_main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "michele", "is_admin": False}),
    )

    with pytest.raises(HTTPException) as denied:
        await gateway_main.circuit_breaker_health(object())

    assert denied.value.status_code == 403


async def test_circuit_breaker_health_lists_break_for_an_admin(monkeypatch):
    reset_breakers()
    monkeypatch.setattr(
        gateway_main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah", "is_admin": True}),
    )

    payload = await gateway_main.circuit_breaker_health(object())

    assert payload == {"breakers": []}
