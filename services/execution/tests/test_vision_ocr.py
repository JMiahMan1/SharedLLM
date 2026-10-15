import os
import sys
import pytest
import respx
from httpx import Response


def _reimport_vision_ocr():
    """Force-reimport vision_ocr with current env by clearing cached modules."""
    for mod in list(sys.modules):
        if mod in ("tools.vision_ocr", "services.config"):
            del sys.modules[mod]


@respx.mock
def test_resolve_ocr_model_raises_when_env_not_set():
    """_resolve_ocr_model raises ValueError when BRIDGE_IDENTITY_SVC_URL is empty."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = ""

    from tools.vision_ocr import _resolve_ocr_model
    with pytest.raises(ValueError, match="OCR model not configured"):
        _resolve_ocr_model()

    # Restore
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"


@respx.mock
def test_resolve_ocr_model_from_settings():
    """_resolve_ocr_model returns value from identity service settings API."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"

    settings_response = [
        {"key": "other_setting", "value": "foo"},
        {"key": "vision_ocr_model", "value": "qwen2.5-vl:7b"},
    ]
    respx.get("http://localhost:8001/api/settings").mock(
        return_value=Response(200, json=settings_response)
    )

    from tools.vision_ocr import _resolve_ocr_model
    model = _resolve_ocr_model()
    assert model == "qwen2.5-vl:7b"


@respx.mock
def test_resolve_ocr_model_not_found_in_settings():
    """_resolve_ocr_model raises ValueError when key not in settings response."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"

    settings_response = [
        {"key": "other_setting", "value": "foo"},
    ]
    respx.get("http://localhost:8001/api/settings").mock(
        return_value=Response(200, json=settings_response)
    )

    from tools.vision_ocr import _resolve_ocr_model
    with pytest.raises(ValueError, match="OCR model not configured"):
        _resolve_ocr_model()


@respx.mock
def test_resolve_ocr_proxy_from_settings():
    """_resolve_ocr_proxy returns value from identity service settings API."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"

    settings_response = [
        {"key": "other_setting", "value": "foo"},
        {"key": "vision_ocr_proxy_url", "value": "http://proxy:7888/"},
    ]
    respx.get("http://localhost:8001/api/settings").mock(
        return_value=Response(200, json=settings_response)
    )

    from tools.vision_ocr import _resolve_ocr_proxy
    proxy = _resolve_ocr_proxy()
    assert proxy == "http://proxy:7888"  # trailing slash stripped


@respx.mock
def test_resolve_ocr_proxy_raises_when_empty():
    """_resolve_ocr_proxy raises ValueError when key not in settings response."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"

    settings_response = [
        {"key": "other_setting", "value": "foo"},
    ]
    respx.get("http://localhost:8001/api/settings").mock(
        return_value=Response(200, json=settings_response)
    )

    from tools.vision_ocr import _resolve_ocr_proxy
    with pytest.raises(ValueError, match="OCR proxy URL not configured"):
        _resolve_ocr_proxy()


@respx.mock
def test_get_cached_ocr_model():
    """_get_cached_ocr_model caches the result and doesn't call API again."""
    _reimport_vision_ocr()
    os.environ["BRIDGE_IDENTITY_SVC_URL"] = "http://localhost:8001"

    settings_response = [
        {"key": "vision_ocr_model", "value": "qwen2.5-vl:7b"},
    ]
    respx.get("http://localhost:8001/api/settings").mock(
        return_value=Response(200, json=settings_response)
    )

    from tools.vision_ocr import _get_cached_ocr_model

    # Clear cache
    import tools.vision_ocr
    tools.vision_ocr._VOCAB_MODEL_CACHE = None

    model1 = _get_cached_ocr_model()
    assert model1 == "qwen2.5-vl:7b"

    # Clear mock to verify cache is used (should not hit API)
    respx.clear()

    # Should still return cached value without hitting API
    model2 = _get_cached_ocr_model()
    assert model2 == "qwen2.5-vl:7b"
