"""Image-reference resolution and registry digest checks (no Docker, no network)."""
from types import SimpleNamespace

import pytest
import requests

from services.control_plane import registry


def _container(config_image=None, tags=(), labels=None, name="sharedllm_ui"):
    return SimpleNamespace(
        name=name,
        attrs={"Config": {"Image": config_image}} if config_image else {},
        labels=labels or {},
        image=SimpleNamespace(tags=list(tags), attrs={}),
    )


class TestContainerImageRef:
    def test_config_image_wins_over_commit_tag(self):
        # The live failure: image.tags[0] was a commit tag that never moves.
        c = _container("ghcr.io/o/sharedllm-ui:latest", tags=["ghcr.io/o/sharedllm-ui:61caa665"])
        assert registry.container_image_ref(c) == "ghcr.io/o/sharedllm-ui:latest"

    def test_image_id_in_config_is_not_a_ref(self):
        c = _container("sha256:abc", tags=["ghcr.io/o/x:61caa665", "ghcr.io/o/x:latest"])
        assert registry.container_image_ref(c) == "ghcr.io/o/x:latest"

    def test_compose_label_before_tags(self):
        c = _container(labels={"com.docker.compose.image": "ghcr.io/o/x:latest"}, tags=["ghcr.io/o/x:1"])
        assert registry.container_image_ref(c) == "ghcr.io/o/x:latest"

    def test_inferred_for_known_service(self, monkeypatch):
        monkeypatch.setenv("GHCR_NAMESPACE", "ns")
        assert registry.container_image_ref(_container(name="sharedllm_bible")) == "ghcr.io/ns/sharedllm-bible:latest"

    def test_unknown_service_has_no_ref(self):
        assert registry.container_image_ref(_container(name="sharedllm_mystery")) is None


@pytest.mark.parametrize("ref,expected", [
    ("ghcr.io/o/sharedllm-ui:latest", ("ghcr.io", "o/sharedllm-ui", "latest")),
    ("ghcr.io/o/sharedllm-ui", ("ghcr.io", "o/sharedllm-ui", "latest")),
    ("redis:7-alpine", ("registry-1.docker.io", "library/redis", "7-alpine")),
    ("mediagis/nominatim:5.3", ("registry-1.docker.io", "mediagis/nominatim", "5.3")),
    ("localhost:5000/app:1", ("localhost:5000", "app", "1")),
])
def test_parse_ref(ref, expected):
    assert registry.parse_ref(ref) == expected


def test_local_digests_lists_every_repo_digest():
    image = SimpleNamespace(attrs={"RepoDigests": ["ghcr.io/o/x@sha256:aa", "ghcr.io/o/x@sha256:bb"]})
    assert registry.local_digests(image) == {"sha256:aa", "sha256:bb"}


class _Resp:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body or {}

    def json(self):
        return self._body


CHALLENGE = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:o/x:pull"'


class TestRemoteDigest:
    def _wire(self, monkeypatch, token_ok_for):
        calls = []

        def head(url, headers=None, timeout=None):
            calls.append(("HEAD", headers.get("Authorization")))
            if headers.get("Authorization") == "Bearer pull-token":
                return _Resp(200, {"Docker-Content-Digest": "sha256:remote"})
            return _Resp(401, {"WWW-Authenticate": CHALLENGE})

        def get(url, params=None, auth=None, timeout=None):
            calls.append(("GET", auth))
            assert url == "https://ghcr.io/token"
            assert params == {"service": "ghcr.io", "scope": "repository:o/x:pull"}
            if auth in token_ok_for:
                return _Resp(200, body={"token": "pull-token"})
            return _Resp(401)

        monkeypatch.setattr(registry.requests, "head", head)
        monkeypatch.setattr(registry.requests, "get", get)
        return calls

    def test_public_image_needs_no_token(self, monkeypatch):
        # GHCR answers 401 to every unauthenticated request, public or not; the
        # old check read that as "auth failed" and never compared a digest.
        self._wire(monkeypatch, token_ok_for=[None])
        assert registry.remote_digest("ghcr.io/o/x:latest") == "sha256:remote"

    def test_rejected_token_falls_back_to_anonymous(self, monkeypatch):
        calls = self._wire(monkeypatch, token_ok_for=[None])
        assert registry.remote_digest("ghcr.io/o/x:latest", token="stale") == "sha256:remote"
        assert ("GET", ("token", "stale")) in calls and ("GET", None) in calls

    def test_private_image_uses_token(self, monkeypatch):
        self._wire(monkeypatch, token_ok_for=[("token", "good")])
        assert registry.remote_digest("ghcr.io/o/x:latest", token="good") == "sha256:remote"

    def test_no_pull_token_means_unknown(self, monkeypatch):
        self._wire(monkeypatch, token_ok_for=[])
        assert registry.remote_digest("ghcr.io/o/x:latest") is None

    def test_unreachable_registry_raises(self, monkeypatch):
        def head(*a, **k):
            raise requests.ConnectionError("dns")
        monkeypatch.setattr(registry.requests, "head", head)
        with pytest.raises(registry.RegistryUnreachable):
            registry.remote_digest("ghcr.io/o/x:latest")
