"""Provider routing + ClinePass support.

Covers the cloud-slot generalization: OpenRouter, OpenAI and ClinePass all
speak /v1/chat/completions and share llm_cloud_url + llm_cloud_api_key, so
selecting a provider in the UI must resolve to that provider's canonical
endpoint without a second setting, while a custom typed URL is still honored.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.gateway.config_validator import validate_config
from services.gateway.llm_providers import (
    CLOUD_PROVIDER_DEFAULT_URLS,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    get_provider,
)


def test_openrouter_alias_points_at_generic_provider():
    assert OpenRouterProvider is OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_get_provider_cline_pass_uses_canonical_url():
    provider = await get_provider({
        "active_llm_provider": "cline_pass",
        "llm_cloud_api_key": "sk_54-test",
        # Stale OpenRouter default left in the field must not leak through.
        "llm_cloud_url": "https://openrouter.ai/api/v1/chat/completions",
    })
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "cline_pass"
    assert provider.base_url == CLOUD_PROVIDER_DEFAULT_URLS["cline_pass"]
    assert provider.api_key == "sk_54-test"


@pytest.mark.asyncio
async def test_get_provider_openrouter_uses_canonical_url():
    provider = await get_provider({"active_llm_provider": "openrouter"})
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.provider_name == "openrouter"
    assert provider.base_url == CLOUD_PROVIDER_DEFAULT_URLS["openrouter"]


@pytest.mark.asyncio
async def test_get_provider_honors_custom_cloud_url():
    provider = await get_provider({
        "active_llm_provider": "cline_pass",
        "llm_cloud_url": "https://my-gateway.internal/v1/chat/completions",
    })
    assert provider.base_url == "https://my-gateway.internal/v1/chat/completions"
    assert provider.provider_name == "cline_pass"


@pytest.mark.asyncio
async def test_get_provider_ollama_requires_local_url():
    with pytest.raises(RuntimeError):
        await get_provider({"active_llm_provider": "ollama", "llm_local_url": ""})


@pytest.mark.asyncio
async def test_get_provider_ollama_returns_local_provider():
    provider = await get_provider({
        "active_llm_provider": "ollama",
        "llm_local_url": "http://localhost:11434",
    })
    assert isinstance(provider, OllamaProvider)


@pytest.mark.asyncio
async def test_get_provider_unknown_falls_back_to_ollama():
    # A provider name that is neither local nor a known cloud slot must not be
    # silently treated as cloud: it degrades to the local provider path.
    provider = await get_provider({
        "active_llm_provider": "mystery",
        "llm_local_url": "http://localhost:11434",
    })
    assert isinstance(provider, OllamaProvider)


class _FakeResponse:
    status = 200

    def raise_for_status(self):
        return None

    async def json(self):
        return {"choices": [{"message": {"content": "hello"}}]}


def _capturing_client(captured: dict) -> MagicMock:
    client = MagicMock()

    async def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["json"] = json
        return _FakeResponse()

    client.post = _post
    return client


async def _generate(provider: OpenAICompatibleProvider, captured: dict) -> str:
    from contextlib import asynccontextmanager

    client = _capturing_client(captured)

    @asynccontextmanager
    async def _fake_shared_client():
        yield client

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.gateway.main.shared_http_client", _fake_shared_client)
        return await provider.generate(
            model="cline-pass/some-model",
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio
async def test_openrouter_sends_referer_header():
    captured: dict = {}
    provider = OpenAICompatibleProvider(api_key="k", provider_name="openrouter")
    result = await _generate(provider, captured)
    assert result == "hello"
    assert captured["headers"]["HTTP-Referer"] == "https://github.com/jmiahman1/sharedllm"
    assert captured["headers"]["Authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_cline_pass_omits_referer_header():
    captured: dict = {}
    provider = OpenAICompatibleProvider(
        api_key="sk_54",
        base_url=CLOUD_PROVIDER_DEFAULT_URLS["cline_pass"],
        provider_name="cline_pass",
    )
    result = await _generate(provider, captured)
    assert result == "hello"
    assert "HTTP-Referer" not in captured["headers"]
    assert captured["url"] == CLOUD_PROVIDER_DEFAULT_URLS["cline_pass"]


def test_config_validator_accepts_cline_pass():
    result = validate_config({
        "active_llm_provider": "cline_pass",
        "identity_svc_url": "http://identity:8000",
        "redis_url": "redis://redis:6379",
        "llm_cloud_api_key": "sk_54",
    })
    # ClinePass is a valid cloud provider: no critical provider failure, and
    # llm_local_url is not demanded while a cloud provider is active.
    assert result.is_functional
    assert not any("active_llm_provider" in f for f in result.critical_failures)
    assert not any("llm_local_url" in f for f in result.required_failures)


def test_config_validator_requires_cloud_key_for_cline_pass():
    result = validate_config({
        "active_llm_provider": "cline_pass",
        "identity_svc_url": "http://identity:8000",
        "redis_url": "redis://redis:6379",
    })
    assert any("llm_cloud_api_key" in f for f in result.optional_failures)


def test_config_validator_rejects_unknown_provider():
    result = validate_config({
        "active_llm_provider": "mystery",
        "identity_svc_url": "http://identity:8000",
        "redis_url": "redis://redis:6379",
        "llm_local_url": "http://localhost:11434",
    })
    assert any("active_llm_provider" in f for f in result.critical_failures)
