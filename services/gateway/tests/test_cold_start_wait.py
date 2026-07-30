"""Tests for OllamaProvider._wait_for_model and _wait_for_slot (cold-start fix).

These guard against the 404s caused by Ollama /api/chat returning 404 until
the model weights finish loading into VRAM. The fix adds a polling pre-step
that checks /api/ps before any streaming request.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.llm_providers import OllamaProvider


def _make_ps_response(status: int, data: dict):
    """Create a response-like object for /api/ps GET requests."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)
    return resp


def _make_iter_any(byte_chunks: bytes):
    """Create an async iterator that yields byte chunks."""
    async def _aiter():
        yield byte_chunks
    _aiter.__name__ = "aiter"
    return _aiter


class _AsyncIter:
    """Mimics aiohttp content.iter_any() by yielding byte chunks."""
    def __init__(self, data: bytes):
        self._data = data

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def iter_any(self):
        yield self._data


def _make_chat_response(status: int, body: bytes, is_streaming: bool = True):
    """Create a response-like object for /api/chat POST requests."""
    resp = MagicMock()
    resp.status = status
    resp.content = MagicMock()
    if is_streaming:
        resp.content.iter_any = _AsyncIter(body).iter_any
    else:
        resp.content.iter_any = AsyncMock(return_value=body)
    resp.text = AsyncMock(return_value=body.decode())
    resp.read = AsyncMock(return_value=body)
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


class TestWaitForModel:
    """Tests for the _wait_for_model cold-start polling logic."""

    @pytest.mark.asyncio
    async def test_returns_true_when_model_in_loaded_list(self):
        """_wait_for_model returns True when the target model appears in /api/ps models list."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {
            "models": [
                {
                    "model": "qwen3:35b",
                    "size": 20_000_000_000,
                    "digest": "abc123",
                    "details": {},
                }
            ],
            "slots": {},
        }
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_non_200_ps(self):
        """When /api/ps returns non-200, _wait_for_model returns True (graceful degradation)."""
        provider = OllamaProvider("http://fake:11434")
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(500, {}))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_model_not_loaded(self):
        """_wait_for_model returns False when the model is NOT in the loaded list."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {"models": [], "slots": {}}
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_string_entry_in_models_list(self):
        """Some Ollama versions return model names as strings instead of dicts."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {"models": ["qwen3:35b", "llama3:8b"], "slots": {}}
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is True

    @pytest.mark.asyncio
    async def test_endswith_match_does_not_false_positive_on_partial(self):
        """Model name matching must use endswith, but 'qwen3:8b' must not match 'qwen3:35b'."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {
            "models": [
                {
                    "model": "qwen3:8b",
                    "size": 5_000_000_000,
                    "digest": "def456",
                    "details": {},
                }
            ],
            "slots": {},
        }
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is False

    @pytest.mark.asyncio
    async def test_matches_model_in_middle_of_list(self):
        """The target model can be anywhere in the loaded models list."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {
            "models": [
                {"model": "llama3:8b", "size": 5_000_000_000, "digest": "aaa", "details": {}},
                {"model": "mistral:7b", "size": 4_500_000_000, "digest": "bbb", "details": {}},
                {"model": "qwen3:35b", "size": 20_000_000_000, "digest": "ccc", "details": {}},
            ],
            "slots": {},
        }
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self):
        """When /api/ps raises an error, _wait_for_model returns False (can't verify model)."""
        provider = OllamaProvider("http://fake:11434")
        session = MagicMock()
        session.get = AsyncMock(side_effect=Exception("connection refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_model(client, "qwen3:35b")
        assert result is False


class TestWaitForSlot:
    """Tests for the updated _wait_for_slot that accepts a model param."""

    @pytest.mark.asyncio
    async def test_slot_wait_passes_model_to_wait_for_model(self):
        """_wait_for_slot(model='x') must call _wait_for_model(client, 'x')."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {
            "models": [
                {
                    "model": "qwen3:35b",
                    "size": 20_000_000_000,
                    "digest": "abc123",
                    "details": {},
                }
            ],
            "slots": {"available": 1},
        }
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_slot(client, model="qwen3:35b")
        assert result is True

    @pytest.mark.asyncio
    async def test_slot_wait_without_model_returns_true(self):
        """_wait_for_slot() without a model still works (no cold-start check)."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {"models": [], "slots": {"available": 1}}
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_slot(client)
        assert result is True

    @pytest.mark.asyncio
    async def test_slot_wait_model_not_loaded_proceeds_anyway(self):
        """When model is not loaded, _wait_for_slot still proceeds (graceful degradation)."""
        provider = OllamaProvider("http://fake:11434")
        ps_response = {"models": [], "slots": {"available": 1}}
        session = MagicMock()
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        async with session as client:
            result = await provider._wait_for_slot(client, model="qwen3:35b")
        assert result is True


class TestGenerateColdStart:
    """Integration-style tests: generate() must call _wait_for_slot with model param."""

    @pytest.mark.asyncio
    async def test_generate_with_model_loaded_succeeds(self):
        """When the model is already loaded, generate() returns content (streaming)."""
        provider = OllamaProvider("http://fake:11434")
        chat_body = b'{"model":"test","message":{"content":"hello"},"done":true}\n'

        session = MagicMock()
        ps_response = {
            "models": [{"model": "qwen3:35b", "size": 20_000_000_000, "digest": "abc", "details": {}}],
            "slots": {"available": 1},
        }
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        chat_resp = _make_chat_response(200, chat_body, is_streaming=True)
        session.post = _make_chat_post(chat_resp)

        with patch("services.gateway.main.shared_http_client", _fake_shared_client(session)):
            collected = []
            async def cb(piece: str):
                collected.append(piece)
            result = await provider.generate(
                model="qwen3:35b",
                messages=[{"role": "user", "content": "hi"}],
                chunk_callback=cb,
            )
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_generate_without_model_in_loaded_proceeds(self):
        """When model not in /api/ps loaded list, generate still attempts (graceful degradation)."""
        provider = OllamaProvider("http://fake:11434")
        chat_body = b'{"model":"test","message":{"content":"hello"},"done":true}\n'

        session = MagicMock()
        ps_response = {"models": [], "slots": {"available": 1}}
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        chat_resp = _make_chat_response(200, chat_body, is_streaming=True)
        session.post = _make_chat_post(chat_resp)

        with patch("services.gateway.main.shared_http_client", _fake_shared_client(session)):
            collected = []
            async def cb(piece: str):
                collected.append(piece)
            result = await provider.generate(
                model="qwen3:35b",
                messages=[{"role": "user", "content": "hi"}],
                chunk_callback=cb,
            )
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_generate_404_on_chat_is_not_raised_for_cold_start(self):
        """A 404 from /api/chat during cold start is NOT swallowed -- it's a provider error."""
        provider = OllamaProvider("http://fake:11434")

        session = MagicMock()
        ps_response = {
            "models": [{"model": "qwen3:35b", "size": 20_000_000_000, "digest": "abc", "details": {}}],
            "slots": {"available": 1},
        }
        session.get = AsyncMock(return_value=_make_ps_response(200, ps_response))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        # Non-streaming response (no chunk_callback)
        chat_body = b'{"error":"model not found"}'
        chat_resp = _make_chat_response(404, chat_body, is_streaming=False)
        chat_resp.status = 404
        session.post = _make_chat_post_nonstreaming(chat_resp)

        with patch("services.gateway.main.shared_http_client", _fake_shared_client(session)), \
                pytest.raises(RuntimeError, match="HTTP 404"):
            await provider.generate(
                    model="qwen3:35b",
                    messages=[{"role": "user", "content": "hi"}],
                    chunk_callback=None,
                )


def _make_chat_post(chat_resp):
    """Create an async context manager for POST that returns a chat response."""
    @asynccontextmanager
    async def _post(*args, **kwargs):
        yield chat_resp
    return _post


def _make_chat_post_nonstreaming(chat_resp):
    """Create a mock that supports both async-with (streaming) and await (non-streaming)."""
    # For non-streaming, the code does: resp = await client.post(...)
    # For streaming, the code does: async with client.post(...) as response:
    post_mock = AsyncMock(return_value=chat_resp)
    post_mock.__aenter__ = AsyncMock(return_value=chat_resp)
    post_mock.__aexit__ = AsyncMock(return_value=None)
    return post_mock


def _fake_shared_client(session):
    @asynccontextmanager
    async def _inner():
        yield session
    return _inner
