import pytest
import httpx
import respx
from fastapi import status

# Phase 4.1: Test HTTP Timeouts
# This test ensures the Gateway handles downstream timeouts gracefully.

@pytest.mark.asyncio
@respx.mock
async def test_gateway_timeout_degradation():
    from services.gateway.main import app
    
    # Mock Identity settings to provide a valid assistant model
    respx.post(url__regex=r".*/api/resolve").return_value = httpx.Response(
        status.HTTP_200_OK,
        json={"user": "jeremiah", "is_admin": True}
    )
    
    # Mock Ollama timeout - covering both generate and chat
    respx.post(url__regex=r".*/api/(generate|chat)").mock(side_effect=httpx.ConnectTimeout)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/chat", json={"query": "hello"})
        
        # In a hardened system, we return a structured degradation instead of 500
        assert resp.status_code == 200
        content = resp.json().get("message", "")
        assert "low-latency mode" in content or "timed out" in content
