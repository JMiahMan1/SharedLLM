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
    
    # Mock Ollama timeout
    respx.post("http://127.0.0.1:11434/api/generate").mock(side_effect=httpx.ConnectTimeout)
    
    # Mock Identity success
    respx.post("http://127.0.0.1:8001/api/resolve").return_value = httpx.Response(
        status.HTTP_200_OK, 
        json={"user": "jeremiah", "is_admin": True}
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/chat", json={"message": "hello"})
        
        # In a hardened system, we return a structured degradation instead of 500
        assert resp.status_code == 200
        assert "Jarvis is currently operating in low-latency mode" in resp.json()["response"] or \
               "Downstream service timed out" in resp.json()["response"]
