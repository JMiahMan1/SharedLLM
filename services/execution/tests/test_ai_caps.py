"""The capability probe has to be trustworthy in both directions.

The failure it exists to prevent is showing a tool as usable when it is not:
the image backend advertises three models and rejects every call. So the tests
pin that a *negative* answer is produced whenever the backend cannot actually
serve an edit, and that an unprovable capability says "unverified" rather than
guessing yes.
"""
import pytest

from services.execution import ai_caps


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _Session:
    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        for frag, resp in self._routes.items():
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected probe url: {url}")


def _patch(monkeypatch, image_routes=None, voice_routes=None, proxy_url="http://proxy:11434"):
    import aiohttp

    from services.execution.handlers import image_edit, vision_ocr

    async def fake_url():
        return proxy_url

    monkeypatch.setattr(vision_ocr, "get_ollama_url", fake_url)
    monkeypatch.setattr(image_edit, "vision_ocr", vision_ocr)
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda *a, **k: _Session({**(image_routes or {}), **(voice_routes or {})}),
    )


@pytest.fixture(autouse=True)
def _clear_observations():
    ai_caps._observations.clear()
    yield
    ai_caps._observations.clear()


def _by_key(caps, key):
    return next(c for c in caps if c["key"] == key)


# --------------------------------------------------------------------------
# Image backend
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_healthy_image_backend_reports_its_models(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={
            "/v1/images/models": _Resp(
                200,
                {"data": [{"id": "qwen-image-edit-rapid-aio:q4_k"}, {"id": "qwen-image-edit-2511:q3_k_s"}]},
            )
        },
        voice_routes={"/health": _Resp(200, {"status": "ok"})},
    )

    caps = await ai_caps.collect_capabilities()
    edit = _by_key(caps, "image_edit")

    assert edit["available"] is True
    assert "qwen-image-edit-rapid-aio:q4_k" in edit["detail"]
    assert "2 model(s)" in edit["detail"]


@pytest.mark.asyncio
async def test_an_unconfigured_image_backend_names_the_setting(monkeypatch):
    """A missing URL must say which setting fixes it, not just 'unavailable'."""
    _patch(monkeypatch, proxy_url=None, voice_routes={"/health": _Resp(200, {})})

    caps = await ai_caps.collect_capabilities()

    assert _by_key(caps, "image_edit")["available"] is False
    assert "llm_local_url" in _by_key(caps, "image_edit")["detail"]


@pytest.mark.asyncio
async def test_a_backend_that_advertises_nothing_is_not_reported_as_working(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": []})},
        voice_routes={"/health": _Resp(200, {})},
    )

    caps = await ai_caps.collect_capabilities()
    assert _by_key(caps, "image_edit")["available"] is False
    assert "no image models" in _by_key(caps, "image_edit")["detail"]


@pytest.mark.asyncio
async def test_an_http_error_from_the_image_backend_is_unavailable(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(503, {})},
        voice_routes={"/health": _Resp(200, {})},
    )
    caps = await ai_caps.collect_capabilities()
    assert _by_key(caps, "image_edit")["available"] is False


@pytest.mark.asyncio
async def test_an_unreachable_image_backend_is_unavailable_not_an_exception(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": ConnectionRefusedError("refused")},
        voice_routes={"/health": _Resp(200, {})},
    )
    caps = await ai_caps.collect_capabilities()
    edit = _by_key(caps, "image_edit")
    assert edit["available"] is False
    assert "unreachable" in edit["detail"]


# --------------------------------------------------------------------------
# Face swap: learned, never assumed
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_face_swap_is_unverified_before_anything_has_been_tried(monkeypatch):
    """Claiming it works would be a lie; claiming it does not would be a guess."""
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )

    caps = await ai_caps.collect_capabilities()
    swap = _by_key(caps, "face_swap")

    assert swap["available"] is None
    assert "Not confirmed yet" in swap["detail"]


@pytest.mark.asyncio
async def test_a_successful_two_image_edit_makes_face_swap_available(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )
    ai_caps.record_observation("face_swap", True, "two-image edit worked", bucket="http://proxy:11434")
    # The bucket is the proxy URL; without a matching probe URL the default
    # bucket is what the handler used when no URL was resolved.
    ai_caps.record_observation("face_swap", True, "two-image edit worked")

    caps = await ai_caps.collect_capabilities()
    assert _by_key(caps, "face_swap")["available"] is True


@pytest.mark.asyncio
async def test_a_rejected_donor_makes_face_swap_unavailable_with_the_reason(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )
    ai_caps.record_observation(
        "face_swap", False, "This backend rejected a second image: no results"
    )

    caps = await ai_caps.collect_capabilities()
    swap = _by_key(caps, "face_swap")

    assert swap["available"] is False
    assert "rejected a second image" in swap["detail"]


@pytest.mark.asyncio
async def test_an_aged_out_observation_is_reported_as_remembered(monkeypatch):
    """Better than 'unknown' -- but it must not be presented as current."""
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )
    ai_caps.record_observation("face_swap", False, "it failed earlier")
    ai_caps._observations["default:face_swap"].seen_at -= ai_caps.OBSERVATION_TTL_S + 1

    caps = await ai_caps.collect_capabilities()
    swap = _by_key(caps, "face_swap")

    assert swap["available"] is None, "a stale observation must not claim to be current"
    assert "Last seen" in swap["detail"]
    assert "it failed earlier" in swap["detail"]


# --------------------------------------------------------------------------
# Voice and video
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_with_nothing_enrolled_is_up_but_says_so(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {}), "/api/voices": _Resp(200, {"profiles": []})},
    )

    caps = await ai_caps.collect_capabilities()
    voice = _by_key(caps, "voice_profiles")

    assert voice["available"] is True
    assert "no voice profiles enrolled" in voice["detail"]


@pytest.mark.asyncio
async def test_voice_counts_enrolled_profiles(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={
            "/health": _Resp(200, {}),
            "/api/voices": _Resp(200, {"profiles": [{"pid": "a"}, {"pid": "b"}]}),
        },
    )
    caps = await ai_caps.collect_capabilities()
    assert "2 enrolled" in _by_key(caps, "voice_profiles")["detail"]


@pytest.mark.asyncio
async def test_a_dead_audio_backend_is_unavailable(monkeypatch):
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": ConnectionRefusedError("refused")},
    )
    caps = await ai_caps.collect_capabilities()
    assert _by_key(caps, "voice_profiles")["available"] is False


@pytest.mark.asyncio
async def test_video_is_reported_as_absent_rather_than_omitted(monkeypatch):
    """A silently missing feature is worse than one that says it isn't here."""
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )
    caps = await ai_caps.collect_capabilities()
    video = _by_key(caps, "video_gen")
    assert video["available"] is False
    assert "No video model" in video["detail"]


@pytest.mark.asyncio
async def test_face_preserve_warns_that_identity_is_approximate(monkeypatch):
    """The drift is real and measured, so the UI must not oversell it."""
    _patch(
        monkeypatch,
        image_routes={"/v1/images/models": _Resp(200, {"data": [{"id": "m"}]})},
        voice_routes={"/health": _Resp(200, {})},
    )
    caps = await ai_caps.collect_capabilities()
    detail = _by_key(caps, "face_preserve")["detail"]
    assert "approximated" in detail
