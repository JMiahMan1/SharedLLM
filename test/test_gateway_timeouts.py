import httpx
import pytest

# Phase 4.1: Test HTTP Timeouts
# This test ensures the Gateway handles unreachable downstreams gracefully.
#
# NOTE: this deliberately does not use respx. respx patches httpx, but the
# gateway talks to Identity and Ollama over aiohttp, so respx mocks never
# intercepted anything here — the test was really exercising the "downstream is
# genuinely unreachable" path all along.
#
# The contract under test is that the gateway degrades in a structured way
# rather than surfacing an unhandled 500 or a stack trace. Which flavour of
# degradation you get depends on how far the request gets: 503 when Identity
# itself cannot be reached, 200 with an explanatory assistant message when it
# can but inference cannot proceed. Both are acceptable; a 500 is not.


def _response_text(payload) -> str:
    """Flatten whichever response dialect came back into searchable text."""
    if not isinstance(payload, dict):
        return str(payload)
    message = payload.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    if isinstance(message, str):
        return message
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content") or "")
    return str(payload.get("detail") or payload)


@pytest.mark.asyncio
async def test_gateway_timeout_degradation():
    from services.gateway.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/chat", json={"query": "hello"})

    # A hardened gateway degrades instead of 500-ing.
    assert resp.status_code in (200, 503), f"unexpected status {resp.status_code}"

    payload = resp.json()
    text = _response_text(payload)
    assert text, f"expected a structured degradation message, got: {payload!r}"
    # It must read as an explanation, not a leaked stack trace.
    assert "Traceback" not in text
