# services/gateway/llm_providers.py
import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from services.gateway.config import OLLAMA_SLOT_POLL_INTERVAL, OLLAMA_SLOT_POLL_MAX

log = logging.getLogger("gateway.providers")

THINKING_PATTERNS = [
    re.compile(r'<think>.*?</think>', re.DOTALL),
    re.compile(r'<think>.*?</think>', re.DOTALL),
    re.compile(r'<thinking>.*?</thinking>', re.DOTALL),
    re.compile(r'<reason>.*?</reason>', re.DOTALL),
]


def strip_thinking_blocks(text: str) -> str:
    """Remove thinking/reasoning blocks from LLM output."""
    result = text
    for pattern in THINKING_PATTERNS:
        result = pattern.sub('', result)
    return result.strip()


class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        chunk_callback: Callable[[str], Awaitable[None]] | None = None
    ) -> str:
        """Standard interface for LLM generation."""
        pass


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, timeout: float | aiohttp.ClientTimeout = 180.0, slot_wait_timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if isinstance(timeout, aiohttp.ClientTimeout) else aiohttp.ClientTimeout(total=timeout)
        self.slot_wait_timeout = slot_wait_timeout

    async def _check_slots(self, client: aiohttp.ClientSession) -> dict | None:
        """Check slot availability via /api/ps. Returns slot info dict or None."""
        try:
            resp = await client.get(f"{self.base_url}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
            if resp.status == 200:
                data = await resp.json()
                return data.get("slots")
        except Exception:
            pass
        return None

    async def _wait_for_slot(self, client: aiohttp.ClientSession) -> bool:
        """Poll /api/ps until a slot is available or timeout.
        If /api/ps has no slot info, returns immediately (no slot mgmt).
        If /api/ps is unreachable, returns True (graceful degradation)."""
        loop = asyncio.get_running_loop()
        try:
            resp = await client.get(f"{self.base_url}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
            if resp.status != 200:
                return True
            data = await resp.json()
            if "slots" not in data:
                log.debug("[OllamaProvider] No slot info in /api/ps, proceeding without wait")
                return True
            slots = data.get("slots", {})
            if slots.get("available", 0) > 0:
                return True
            # Slots are busy — poll until one opens (capped exponential backoff)
            deadline = loop.time() + self.slot_wait_timeout
            poll_interval = OLLAMA_SLOT_POLL_INTERVAL
            while loop.time() < deadline:
                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * 2, OLLAMA_SLOT_POLL_MAX)
                resp2 = await client.get(f"{self.base_url}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
                if resp2.status == 200:
                    d2 = await resp2.json()
                    s2 = d2.get("slots", {})
                    if s2.get("available", 0) > 0:
                        log.info("[OllamaProvider] Slot available after waiting")
                        return True
            log.warning(f"[OllamaProvider] Timed out waiting for slot after {self.slot_wait_timeout}s")
            return False
        except Exception as e:
            log.warning(f"[OllamaProvider] Could not check slots ({e}), proceeding anyway")
            return True

    async def generate(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        chunk_callback: Callable[[str], Awaitable[None]] | None = None
    ) -> str:
        from services.gateway.main import shared_http_client
        # Queue-and-wait: check if Ollama has available slots before submitting
        async with shared_http_client() as slot_client:
            if not await self._wait_for_slot(slot_client):
                raise RuntimeError(f"No slots available within {self.slot_wait_timeout}s")

        opts = options or {}
        show_thinking = opts.get("show_thinking", False)

        payload = {
            "model": model,
            "messages": messages,
            "stream": chunk_callback is not None,  # Only stream when caller expects chunks
            "options": opts
        }

        full_content = ""
        async with shared_http_client() as client:
            log.info(f"[OllamaProvider] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload, headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout)
                if resp.status >= 400:
                    raw_text = await resp.text()
                    raise RuntimeError(f"Ollama HTTP {resp.status}: {raw_text}")
                resp.raise_for_status()

                # Harden: Strip keep-alive spaces and handle potential multi-line/streamed JSON
                raw_text = (await resp.text()).strip()
                if not raw_text:
                    return ""

                # If the response contains multiple JSON objects (NDJSON), take the last one or merge
                if "\n" in raw_text:
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    content = ""
                    for line in lines:
                        try:
                            data = json.loads(line)
                            if "error" in data:
                                content += f" [PROVIDER ERROR: {data['error']}] "
                            msg = data.get("message", {})
                            chunk = msg.get("content") or ""
                            content += chunk
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                    # Only fall back to thinking when the caller explicitly requested it;
                    # otherwise a thinking-only response (e.g. a degenerate internal
                    # "draft" loop) must NOT be surfaced as the final answer.
                    if not content.strip() and show_thinking:
                        for line in lines:
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})
                                content += msg.get("thinking") or ""
                            except json.JSONDecodeError:
                                continue
                    # Strip thinking blocks unless explicitly requested
                    if not show_thinking:
                        content = strip_thinking_blocks(content)
                    return content

                try:
                    data = json.loads(raw_text)
                    if "error" in data:
                        return f" [PROVIDER ERROR: {data['error']}] "
                    msg = data.get("message", {})
                    content = msg.get("content") or ""
                    # Only fall back to thinking when the caller explicitly requested it;
                    # otherwise a thinking-only response must not be surfaced as the answer.
                    if not content.strip() and show_thinking:
                        content = msg.get("thinking") or ""
                    # Strip thinking blocks unless explicitly requested
                    if not show_thinking:
                        content = strip_thinking_blocks(content)
                    return content
                except json.JSONDecodeError as e:
                    log.error(f"[OllamaProvider] Failed to parse JSON: {raw_text[:100]}... Error: {e}")
                    return ""

            # Streaming
            async with client.post(f"{self.base_url}/api/chat", json=payload, headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout) as response:
                if response.status >= 400:
                    await response.read()
                    raise RuntimeError(f"Ollama stream HTTP {response.status}: {await response.text()}")
                response.raise_for_status()
                # Ollama streams newline-delimited JSON (NDJSON). iter_any() yields
                # arbitrary byte chunks that may contain MULTIPLE JSON objects (or a
                # partial one) per read. Buffer across reads and parse per complete
                # line, otherwise json.loads() throws "Extra data" and silently drops
                # tokens -> garbled/truncated tool-call JSON (real mission failure).
                buffer = ""
                stream_done = False
                async for chunk in response.content.iter_any():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        raw_line, buffer = buffer.split("\n", 1)
                        clean_line = raw_line.strip()
                        if not clean_line:
                            continue
                        try:
                            chunk_json = json.loads(clean_line)
                            if "error" in chunk_json:
                                raise RuntimeError(f"Provider error: {chunk_json['error']}")
                            msg = chunk_json.get("message", {})
                            piece = msg.get("content") or ""
                            # Only include thinking if explicitly requested
                            if not piece and show_thinking:
                                piece = msg.get("thinking") or ""
                            if piece:
                                full_content += piece
                                await chunk_callback(piece)
                            if chunk_json.get("done"):
                                stream_done = True
                                break
                        except RuntimeError:
                            raise  # Let provider errors propagate to AgentLoop retry logic
                        except Exception as e:
                            log.error(f"Error parsing streaming chunk: {e} | Raw line: {clean_line!r}")
                    if stream_done:
                        break
                # Flush any trailing complete object left without a newline terminator.
                tail = buffer.strip()
                if tail and not stream_done:
                    try:
                        chunk_json = json.loads(tail)
                        msg = chunk_json.get("message", {})
                        piece = msg.get("content") or ""
                        if not piece and show_thinking:
                            piece = msg.get("thinking") or ""
                        if piece:
                            full_content += piece
                            await chunk_callback(piece)
                    except Exception as e:
                        log.error(f"Error parsing trailing streaming chunk: {e} | Raw: {tail!r}")
        # Strip thinking blocks from final content unless explicitly requested
        if not show_thinking:
            full_content = strip_thinking_blocks(full_content)
        return full_content


class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1/chat/completions", timeout: float | aiohttp.ClientTimeout = 120.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout if isinstance(timeout, aiohttp.ClientTimeout) else aiohttp.ClientTimeout(total=timeout)

    async def generate(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        chunk_callback: Callable[[str], Awaitable[None]] | None = None
    ) -> str:
        from services.gateway.main import shared_http_client
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jmiahman1/sharedllm",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": options.get("temperature", 0.7) if options else 0.7,
            "stream": bool(chunk_callback)
        }
        # Forward enable_thinking if set — disables qwen3-style extended reasoning
        if options and "enable_thinking" in options:
            payload["enable_thinking"] = options["enable_thinking"]

        full_content = ""
        async with shared_http_client() as client:
            log.info(f"[OpenRouterProvider] Calling {self.base_url} for model {model}")
            if not chunk_callback:
                resp = await client.post(self.base_url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status >= 400:
                    raw_text = await resp.text()
                    raise RuntimeError(f"OpenRouter HTTP {resp.status}: {raw_text}")
                resp.raise_for_status()
                data = await resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = msg.get("content", "") or ""
                reasoning = msg.get("reasoning_content", "") or ""
                # Return only the visible content; fall back to reasoning if model
                # put its entire answer in the thinking block (some model configs do this)
                return content if content.strip() else reasoning

            # Streaming — reasoning_content is internal thinking, do NOT stream it
            # to chunk_callback. Accumulate separately as a fallback only.
            full_content = ""
            full_reasoning = ""
            async with client.post(self.base_url, json=payload, headers=headers, timeout=self.timeout) as response:
                if response.status >= 400:
                    await response.read()
                    raise RuntimeError(f"OpenRouter stream HTTP {response.status}: {response.text}")
                response.raise_for_status()
                async for chunk in response.content.iter_any():
                    line = chunk.decode("utf-8")
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content") or ""
                            reasoning = delta.get("reasoning_content") or ""

                            if reasoning:
                                full_reasoning += reasoning

                            if content:
                                full_content += content
                                await chunk_callback(content)
                        except Exception as e:
                            log.error(f"Error parsing streaming chunk: {e}")

            # If the model never emitted content (thinking-only response), fall back
            # to the accumulated reasoning so the AgentLoop can still extract a JSON action.
            if not full_content.strip() and full_reasoning.strip():
                log.warning("[OpenRouterProvider] No content chunks received; falling back to reasoning_content")
                return full_reasoning
            return full_content
