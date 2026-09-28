"""Shared fixtures for media endpoint tests.

Provides:
- ``fake_identity``: patches ``services.gateway.main.resolve_identity`` so
  media endpoints resolve a fixed user without a live identity service.
- ``client``: authenticated TestClient for the gateway app.
- ``upstream``: aioresponses mock for upstream MA/ABS/HA HTTP calls.
"""
# aioresponses 0.7.9 predates aiohttp 3.14, where ClientResponse gained a
# required `stream_writer` kwarg, so a mocked response cannot be constructed.
# The shim is applied ONLY when the installed aiohttp actually takes that
# kwarg: injecting it unconditionally breaks aiohttp < 3.14, whose
# ClientResponse has no such parameter, and the resulting TypeError
# ("unexpected keyword argument 'stream_writer'") reads like an application
# bug rather than a harness one. Detected by signature rather than by version
# number so an aiohttp that adds or drops the parameter does not need a code
# change here.
import inspect
import re
import sys

# Forced, not setdefault: the root conftest already sets INTERNAL_SECRET to
# test-secret-ci, and services.gateway.config freezes INTERNAL_SECRET and
# ALPACA_AUDIO_URL at its first import — whichever test module imports
# gateway main first. This conftest loads before every module in this
# directory, so pin the gateway test values here (401/503 in
# test_music_proxy.py otherwise, depending on file order).
from unittest.mock import MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses
from fastapi.testclient import TestClient

_NEEDS_STREAM_WRITER = "stream_writer" in inspect.signature(aiohttp.ClientResponse.__init__).parameters

if _NEEDS_STREAM_WRITER and not getattr(aiohttp.ClientResponse.__init__, "_stream_writer_shim", False):

    class _StreamWriterStub:
        output_size = 0

    _orig_client_response_init = aiohttp.ClientResponse.__init__

    def _client_response_init(self, *args, **kwargs):
        kwargs.setdefault("stream_writer", _StreamWriterStub())
        return _orig_client_response_init(self, *args, **kwargs)

    _client_response_init._stream_writer_shim = True
    aiohttp.ClientResponse.__init__ = _client_response_init

# Heavy optional dependencies that main.py imports at module level. Stub them
# before importing the app so tests don't need the real packages.
sys.modules.setdefault("fastembed", MagicMock())
if "intent_engine" not in sys.modules:
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_engine
sys.modules.setdefault("background_worker", MagicMock())

TEST_USER = "testuser"
TEST_MASS_URL = "http://ma.local:8095"
TEST_MASS_TOKEN = "test-mass-token"
TEST_ABS_URL = "http://abs.local:13378"
TEST_ABS_KEY = "test-abs-key"
TEST_HA_URL = "http://ha.local:8123"
TEST_HA_TOKEN = "test-ha-token"

DEFAULT_IDENTITY = {
    "user": TEST_USER,
    "username": TEST_USER,
    "user_id": 1,
    "mass_url": TEST_MASS_URL,
    "mass_token": TEST_MASS_TOKEN,
    "audiobookshelf_url": TEST_ABS_URL,
    "audiobookshelf_api_key": TEST_ABS_KEY,
    "ha_url": TEST_HA_URL,
    "ha_token": TEST_HA_TOKEN,
    "is_admin": True,
}


@pytest.fixture
def fake_identity(monkeypatch):
    """Resolve every identity lookup to DEFAULT_IDENTITY."""
    from services.gateway import main

    async def _resolve(body):
        return dict(DEFAULT_IDENTITY)

    monkeypatch.setattr(main, "resolve_identity", _resolve)
    return DEFAULT_IDENTITY


# orchestrator._sync_main_constants() writes fetched settings into main.py's
# module globals at request time. Tests that mock /api/settings with the plain
# EXECUTION_SVC_URL env var (e.g. test_chat_storage_routing.py) therefore
# pollute main.EXECUTION_SVC for the rest of the session. Pin the synced
# constants to the canonical config values so media tests are order-independent.
_SYNCED_CONSTANTS = (
    "EXECUTION_SVC",
    "IDENTITY_SVC",
    "RAG_SVC",
    "STORAGE_SVC",
    "LOGGING_SVC",
    "WORKSPACE_RUNTIME_SVC",
    "CONTROL_PLANE_URL",
    "OLLAMA_URL",
)


@pytest.fixture
def client(fake_identity):
    """Authenticated TestClient; identity resolution is faked."""
    import services.gateway.config as gw_config
    from services.gateway import main
    from services.gateway.main import app

    for attr in _SYNCED_CONSTANTS:
        if hasattr(gw_config, attr):
            setattr(main, attr, getattr(gw_config, attr))

    return TestClient(
        app,
        headers={
            "Authorization": "Bearer test-token",
            "X-API-Key": "test-token",
        },
    )


@pytest.fixture
def upstream():
    """Intercept upstream MA/ABS/HA HTTP calls."""
    with aioresponses() as mock:
        yield mock


def mock_upstream(
    upstream,
    method: str,
    url: str,
    payload=None,
    status: int = 200,
    headers=None,
    body=None,
    content_type=None,
    repeat: int = 1,
):
    """Query-string-agnostic upstream mock.

    aioresponses matches exact URLs, so calls made with params (e.g.
    ``?user_id=...``) never match a bare path. Register a regex instead.

    ``payload`` is JSON-encoded by aioresponses; use ``body`` (str or bytes)
    with ``content_type`` for raw (non-JSON) upstream responses.

    ``repeat`` reuses the same response for N calls — aioresponses raises
    ``ClientConnectionError`` once a registration is exhausted, which is how
    polling code is tested (a 404 that never turns into a 200).
    """
    pattern = re.compile(re.escape(url) + r".*")
    verb = getattr(upstream, method.lower())
    kwargs = {"status": status, "repeat": repeat}
    if payload is not None:
        kwargs["payload"] = payload
    if body is not None:
        kwargs["body"] = body
    if headers:
        kwargs["headers"] = headers
    if content_type:
        kwargs["content_type"] = content_type
    verb(pattern, **kwargs)
    return pattern
