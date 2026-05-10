# services/gateway/llm_providers.py
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

log = logging.getLogger("gateway.providers")


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
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True if chunk_callback else False,
            "options": options or {}
        }

        full_content = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            log.info(f"[OllamaProvider] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                return resp.json().get("message", {}).get("content", "")

            # Streaming
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk_json = json.loads(line)
                        content = chunk_json.get("message", {}).get("content", "")
                        if content:
                            full_content += content
                            await chunk_callback(content)
                        if chunk_json.get("done"):
                            break
                    except Exception as e:
                        log.error(f"Error parsing streaming chunk: {e}")
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

        full_content = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            log.info(f"[OpenRouterProvider] Calling {self.base_url} for model {model}")
            if not chunk_callback:
                resp = await client.post(self.base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Streaming
            async with client.stream("POST", self.base_url, json=payload, headers=headers) as response:
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
                            content = chunk_json.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                full_content += content
                                await chunk_callback(content)
                        except Exception as e:
                            log.error(f"Error parsing streaming chunk: {e}")
        return full_content
