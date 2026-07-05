# services/gateway/llm_providers.py
import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

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
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        """Standard interface for LLM generation."""
        pass


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, timeout: float = 180.0, slot_wait_timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.slot_wait_timeout = slot_wait_timeout

    async def _check_slots(self, client: aiohttp.ClientSession) -> Optional[dict]:
        """Check slot availability via /api/ps. Returns slot info dict or None."""
        try:
            resp = await client.get(f"{self.base_url}/api/ps", timeout=3.0)
            if resp.status == 200:
                data = resp.json()
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
            resp = await client.get(f"{self.base_url}/api/ps", timeout=3.0)
            if resp.status != 200:
                return True
            data = resp.json()
            if "slots" not in data:
                log.debug(f"[OllamaProvider] No slot info in /api/ps, proceeding without wait")
                return True
            slots = data.get("slots", {})
            if slots.get("available", 0) > 0:
                return True
            # Slots are busy — poll until one opens
            deadline = loop.time() + self.slot_wait_timeout
            poll_interval = 1.0
            while loop.time() < deadline:
                await asyncio.sleep(poll_interval)
                resp2 = await client.get(f"{self.base_url}/api/ps", timeout=3.0)
                if resp2.status == 200:
                    d2 = resp2.json()
                    s2 = d2.get("slots", {})
                    if s2.get("available", 0) > 0:
                        log.info(f"[OllamaProvider] Slot available after waiting")
                        return True
            log.warning(f"[OllamaProvider] Timed out waiting for slot after {self.slot_wait_timeout}s")
            return False
        except Exception as e:
            log.warning(f"[OllamaProvider] Could not check slots ({e}), proceeding anyway")
            return True

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        # Queue-and-wait: check if Ollama has available slots before submitting
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as slot_client:
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
        async with aiohttp.ClientSession(headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout) as client:
            log.info(f"[OllamaProvider] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                if resp.status >= 400:
                    raise RuntimeError(f"Ollama HTTP {resp.status}: {resp.text}")
                resp.raise_for_status()
                
                # Harden: Strip keep-alive spaces and handle potential multi-line/streamed JSON
                raw_text = resp.text.strip()
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
                    # Only fall back to thinking if content is completely empty
                    if not content.strip():
                        for line in lines:
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})
                                thinking = msg.get("thinking") or ""
                                content += thinking
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
                    # Only fall back to thinking if content is completely empty
                    if not content.strip():
                        content = msg.get("thinking") or ""
                    # Strip thinking blocks unless explicitly requested
                    if not show_thinking:
                        content = strip_thinking_blocks(content)
                    return content
                except json.JSONDecodeError as e:
                    log.error(f"[OllamaProvider] Failed to parse JSON: {raw_text[:100]}... Error: {e}")
                    return ""

            # Streaming
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                if response.status >= 400:
                    await response.aread()
                    raise RuntimeError(f"Ollama stream HTTP {response.status}: {response.text}")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    try:
                        chunk_json = json.loads(clean_line)
                        if "error" in chunk_json:
                            raise RuntimeError(f"Provider error: {chunk_json['error']}")
                        msg = chunk_json.get("message", {})
                        chunk = msg.get("content") or ""
                        # Only include thinking if explicitly requested
                        if not chunk and show_thinking:
                            chunk = msg.get("thinking") or ""
                        if chunk:
                            full_content += chunk
                            await chunk_callback(chunk)
                        if chunk_json.get("done"):
                            break

                    except RuntimeError:
                        raise  # Let provider errors propagate to AgentLoop retry logic
                    except Exception as e:
                        log.error(f"Error parsing streaming chunk: {e} | Raw line: {line!r}")
        # Strip thinking blocks from final content unless explicitly requested
        if not show_thinking:
            full_content = strip_thinking_blocks(full_content)
        return full_content


class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1/chat/completions", timeout: float = 120.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jmiahman1/sharedllm",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": options.get("temperature", 0.7) if options else 0.7,
            "stream": True if chunk_callback else False
        }
        # Forward enable_thinking if set — disables qwen3-style extended reasoning
        if options and "enable_thinking" in options:
            payload["enable_thinking"] = options["enable_thinking"]

        full_content = ""
        async with aiohttp.ClientSession(timeout=self.timeout) as client:
            log.info(f"[OpenRouterProvider] Calling {self.base_url} for model {model}")
            if not chunk_callback:
                resp = await client.post(self.base_url, json=payload, headers=headers)
                if resp.status >= 400:
                    raise RuntimeError(f"OpenRouter HTTP {resp.status}: {resp.text}")
                resp.raise_for_status()
                data = resp.json()
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
            async with client.stream("POST", self.base_url, json=payload, headers=headers) as response:
                if response.status >= 400:
                    await response.aread()
                    raise RuntimeError(f"OpenRouter stream HTTP {response.status}: {response.text}")
                response.raise_for_status()
                async for line in response.aiter_lines():
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
