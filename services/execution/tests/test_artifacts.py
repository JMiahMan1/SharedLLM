"""Generated media lands in the Artifact library without risking the render."""
import base64
import json

from services.execution import artifacts


class _Context:
    def __init__(self, user: str):
        self.user = user


def _response(status: int, body: dict | str):
    return {"status_code": status, "text": body if isinstance(body, str) else json.dumps(body)}


async def test_generated_bytes_are_uploaded_for_the_speaking_user(monkeypatch):
    seen = {}

    async def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return _response(201, {"id": "abc", "name": "narration.wav"})

    monkeypatch.setattr(artifacts, "http_request", fake_request)
    monkeypatch.setattr(artifacts, "STORAGE_SVC_URL", "http://storage:8005")
    monkeypatch.setattr(artifacts, "INTERNAL_SECRET", "secret")

    result = await artifacts.create_artifact(
        user_context=_Context("jeremiah"),
        kind="tts_audio",
        name="/tmp/narration.wav",
        data=b"RIFF",
        mime_type="audio/wav",
        workspace_id="ws-1",
        generator="kokoro_tts",
        metadata={"voice": "af_heart"},
    )

    assert result == {"id": "abc", "name": "narration.wav"}
    assert seen["method"] == "POST"
    assert seen["url"] == "http://storage:8005/artifacts"
    assert seen["headers"] == {"X-Internal-Secret": "secret", "X-User": "jeremiah"}
    assert seen["json"]["name"] == "narration.wav"
    assert base64.b64decode(seen["json"]["content_b64"]) == b"RIFF"
    assert seen["json"]["workspace_id"] == "ws-1"


async def test_shared_kiosk_identities_do_not_own_artifacts(monkeypatch):
    called = False

    async def fake_request(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("storage should not be contacted")

    monkeypatch.setattr(artifacts, "http_request", fake_request)
    for user in ("household", "anonymous", ""):
        assert await artifacts.create_artifact(
            user_context={"user": user},
            kind="tts_audio",
            name="speech.wav",
            data=b"RIFF",
        ) is None
    assert called is False


async def test_a_storage_failure_does_not_lose_the_render(monkeypatch):
    async def rejected(*args, **kwargs):
        return _response(507, {"detail": "quota full"})

    monkeypatch.setattr(artifacts, "http_request", rejected)
    assert await artifacts.create_artifact(
        user_context=_Context("jeremiah"),
        kind="tts_audio",
        name="speech.wav",
        data=b"RIFF",
    ) is None


async def test_an_unreachable_storage_service_is_swallowed(monkeypatch):
    async def broken(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(artifacts, "http_request", broken)
    assert await artifacts.create_artifact(
        user_context=_Context("jeremiah"),
        kind="tts_audio",
        name="speech.wav",
        data=b"RIFF",
    ) is None


def test_the_library_name_is_only_the_basename():
    assert artifacts.artifact_name("/tmp/../secret/narration.wav", "speech.wav") == "narration.wav"
    assert artifacts.artifact_name("", "speech.wav") == "speech.wav"
