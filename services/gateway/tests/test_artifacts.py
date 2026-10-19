"""Gateway-generated images reach the Artifact library without breaking the tool."""
import base64
import json

from services.gateway import artifacts


class _Context:
    def __init__(self, user: str):
        self.user = user


class _Response:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        self._body = body if isinstance(body, str) else json.dumps(body)

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, response: _Response):
        self.response = response
        self.seen = None

    def post(self, url, **kwargs):
        self.seen = {"url": url, **kwargs}
        return self.response


async def test_generated_bytes_are_uploaded_for_the_speaking_user(monkeypatch):
    client = _Client(_Response(201, {"id": "abc", "name": "scene.png"}))
    monkeypatch.setattr(artifacts, "get_client", lambda: client)
    monkeypatch.setattr(artifacts, "STORAGE_SVC", "http://storage:8005")
    monkeypatch.setattr(artifacts, "INTERNAL_SECRET", "secret")

    result = await artifacts.create_artifact(
        user_context=_Context("jeremiah"),
        kind="image",
        name="/tmp/scene.png",
        data=b"\x89PNG",
        mime_type="image/png",
        workspace_id="ws-1",
        generator="alpaca_sd",
        metadata={"prompt": "a red door"},
    )

    assert result == {"id": "abc", "name": "scene.png"}
    assert client.seen["url"] == "http://storage:8005/artifacts"
    assert client.seen["headers"] == {"X-Internal-Secret": "secret", "X-User": "jeremiah"}
    assert client.seen["json"]["name"] == "scene.png"
    assert base64.b64decode(client.seen["json"]["content_b64"]) == b"\x89PNG"
    assert client.seen["json"]["workspace_id"] == "ws-1"
    assert client.seen["json"]["metadata"] == {"prompt": "a red door"}


async def test_shared_kiosk_identities_do_not_own_artifacts(monkeypatch):
    def called():
        raise AssertionError("storage should not be contacted")

    monkeypatch.setattr(artifacts, "get_client", called)
    for user in ("household", "anonymous", ""):
        assert await artifacts.create_artifact(
            user_context={"user": user},
            kind="image",
            name="scene.png",
            data=b"\x89PNG",
        ) is None


async def test_a_storage_failure_does_not_lose_the_image(monkeypatch):
    client = _Client(_Response(507, {"detail": "quota full"}))
    monkeypatch.setattr(artifacts, "get_client", lambda: client)

    assert await artifacts.create_artifact(
        user_context=_Context("jeremiah"),
        kind="image",
        name="scene.png",
        data=b"\x89PNG",
    ) is None


def test_the_library_name_is_only_the_basename():
    assert artifacts.artifact_name("/tmp/../secret/scene.png", "image.png") == "scene.png"
    assert artifacts.artifact_name("", "image.png") == "image.png"
