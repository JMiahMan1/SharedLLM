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
    re.compile(r'<(?:think|thinking|reason|thought)>.*?(?:</(?:think|thinking|reason|thought)>|$)', re.DOTALL | re.IGNORECASE),
]


def strip_thinking_blocks(text: str) -> str:
    """Remove thinking/reasoning blocks from LLM output."""
    result = text
    for pattern in THINKING_PATTERNS:
        result = pattern.sub('', result)
    return result.strip()


def extract_thinking_and_content(text: str) -> tuple[str, str]:
    """Separate thinking blocks from clean content. Returns (thinking, clean_content)."""
    if not text:
        return ("", "")
    thinks = re.findall(r'<(?:think|thinking|reason)>([\s\S]*?)(?:</(?:think|thinking|reason)>|$)', text, flags=re.IGNORECASE)
    thinking = "\n".join(t.strip() for t in thinks if t.strip())
    content = strip_thinking_blocks(text)
    return (thinking, content)


class StreamingThinkingFilter:
    """Filters out <think>...</think> and <thinking>...</thinking> tags from a streaming token stream."""
    def __init__(self):
        self._in_think = False
        self._buf = ""

    def process_with_thinking(self, chunk: str) -> tuple[str, str]:
        if not chunk:
            return ("", "")
        self._buf += chunk
        clean_out = []
        think_out = []
        while self._buf:
            if not self._in_think:
                m = re.search(r'<(think|thinking|reason)>', self._buf, re.IGNORECASE)
                if m:
                    clean_out.append(self._buf[:m.start()])
                    self._in_think = True
                    self._buf = self._buf[m.end():]
                else:
                    partial = re.search(r'<[a-z]{0,8}$', self._buf, re.IGNORECASE)
                    if partial:
                        clean_out.append(self._buf[:partial.start()])
                        self._buf = self._buf[partial.start():]
                        break
                    else:
                        clean_out.append(self._buf)
                        self._buf = ""
                        break
            else:
                m = re.search(r'</(think|thinking|reason)>', self._buf, re.IGNORECASE)
                if m:
                    think_out.append(self._buf[:m.start()])
                    self._in_think = False
                    self._buf = self._buf[m.end():]
                else:
                    partial = re.search(r'</[a-z]{0,8}$', self._buf, re.IGNORECASE)
                    if partial:
                        think_out.append(self._buf[:partial.start()])
                        self._buf = self._buf[partial.start():]
                        break
                    else:
                        think_out.append(self._buf)
                        self._buf = ""
                        break
        return ("".join(clean_out), "".join(think_out))

    def process(self, chunk: str) -> str:
        clean, _ = self.process_with_thinking(chunk)
        return clean

    def flush(self) -> str:
        clean, _ = self.flush_both()
        return clean

    def flush_both(self) -> tuple[str, str]:
        if not self._in_think and self._buf:
            res = self._buf
            self._buf = ""
            return (res, "")
        elif self._in_think and self._buf:
            res = self._buf
            self._buf = ""
            return ("", res)
        self._buf = ""
        return ("", "")


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

    async def _wait_for_model(self, client: aiohttp.ClientSession, model: str) -> bool:
        """Poll /api/ps until the target model appears in loaded models.

        Prevents 404s when the model is cold-loaded -- /api/chat returns 404
        until Ollama has finished loading the weights into VRAM.
        """
        try:
            resp = await client.get(f"{self.base_url}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
            if resp.status != 200:
                return True
            data = await resp.json()
            loaded = data.get("models") or data.get("slots") or []
            for entry in loaded:
                if isinstance(entry, dict) and entry.get("model", "").endswith(model):
                    log.info(
                        f"[OllamaProvider] Model {model} loaded "
                        f"({entry.get('size', 0) / 1e9:.1f} GB)"
                    )
                    return True
                elif isinstance(entry, str) and entry == model:
                    log.info(f"[OllamaProvider] Model {model} loaded")
                    return True
        except Exception:
            pass
        return False

    async def _wait_for_slot(self, client: aiohttp.ClientSession, model: str = "") -> bool:
        """Poll /api/ps until a slot is available or timeout.
        If /api/ps has no slot info, returns immediately (no slot mgmt).
        If /api/ps is unreachable, returns True (graceful degradation).
        If model name is provided, waits for model to be loaded first."""
        loop = asyncio.get_running_loop()

        # Wait for model to be loaded before checking slots
        if model:
            loaded = await self._wait_for_model(client, model)
            if not loaded:
                log.warning(f"[OllamaProvider] Model {model} may not be fully loaded, proceeding anyway")

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
            if not await self._wait_for_slot(slot_client, model=model):
                raise RuntimeError(f"No slots available within {self.slot_wait_timeout}s")

        opts = options or {}
        show_thinking = opts.get("show_thinking", False)

        payload = {
            "model": model,
            "messages": messages,
            "stream": chunk_callback is not None,  # Only stream when caller expects chunks
            "options": opts
        }
        # Let Alpaca's shared slot queue absorb the wait instead of failing fast
        # when every slot is busy (background report runs set this explicitly).
        queue_timeout = opts.get("queue_timeout")
        if queue_timeout:
            payload["queue_timeout"] = float(queue_timeout)
        opts.pop("queue_timeout", None)
        opts.pop("slot_wait_timeout", None)
        if not show_thinking:
            payload["think"] = False
            payload["enable_thinking"] = False
            opts["think"] = False
            opts["enable_thinking"] = False

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
                    # Content must NEVER contain thinking blocks
                    content = strip_thinking_blocks(content)
                    return content

                try:
                    data = json.loads(raw_text)
                    if "error" in data:
                        return f" [PROVIDER ERROR: {data['error']}] "
                    msg = data.get("message", {})
                    content = msg.get("content") or ""
                    # Content must NEVER contain thinking blocks
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
                think_filter = StreamingThinkingFilter()
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
                            thinking_piece = msg.get("thinking") or ""

                            clean_piece, think_from_piece = think_filter.process_with_thinking(piece)
                            full_thinking = thinking_piece or think_from_piece

                            if show_thinking and full_thinking and chunk_callback:
                                await chunk_callback({"type": "thinking", "text": full_thinking})

                            if clean_piece:
                                full_content += clean_piece
                                if chunk_callback:
                                    await chunk_callback({"type": "content", "text": clean_piece})

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
                        thinking_piece = msg.get("thinking") or ""
                        clean_piece, think_from_piece = think_filter.process_with_thinking(piece)
                        full_thinking = thinking_piece or think_from_piece
                        if show_thinking and full_thinking and chunk_callback:
                            await chunk_callback({"type": "thinking", "text": full_thinking})
                        if clean_piece:
                            full_content += clean_piece
                            if chunk_callback:
                                await chunk_callback({"type": "content", "text": clean_piece})
                    except Exception as e:
                        log.error(f"Error parsing trailing streaming chunk: {e} | Raw: {tail!r}")

                tail_clean, tail_think = think_filter.flush_both()
                if show_thinking and tail_think and chunk_callback:
                    await chunk_callback({"type": "thinking", "text": tail_think})
                if tail_clean:
                    full_content += tail_clean
                    if chunk_callback:
                        await chunk_callback({"type": "content", "text": tail_clean})
        # Content returned must NEVER contain thinking blocks
        full_content = strip_thinking_blocks(full_content)
        return full_content.strip()


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


async def get_provider(settings: dict[str, Any]) -> BaseLLMProvider:
    """Instantiates the correct provider based on settings."""
    active_provider = settings.get("active_llm_provider", "ollama")
    timeout_raw = settings.get("ollama_timeout", "600")
    try:
        timeout = float(timeout_raw)
    except (ValueError, TypeError):
        timeout = 600.0
    if active_provider == "openrouter":
        return OpenRouterProvider(
            api_key=settings.get("llm_cloud_api_key", ""),
            base_url=settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions"),
            timeout=timeout,
        )
    else:
        local_url = settings.get("llm_local_url", "")
        if not local_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        return OllamaProvider(
            base_url=local_url,
            timeout=timeout,
        )

