"""
Tests for GHCR token resolution fallback logic.

Verifies that control_plane resolves tokens in the correct order:
1. Identity service github_token for user ID 1
2. GHCR_TOKEN environment variable
3. GITHUB_TOKEN environment variable
"""

import os

import pytest


@pytest.fixture
def control_plane_code():
    """Load the control_plane main.py code."""
    with open(
        os.path.join(os.path.dirname(__file__), "..", "main.py"),
    ) as f:
        return f.read()


class TestTokenResolutionFallback:
    """_github_token(): Identity's token for user 1, else GHCR_TOKEN, else
    GITHUB_TOKEN. (Behaviour, not source text: the lookup moved into one
    helper shared by update checks and the road-map automation.)"""

    @staticmethod
    def _identity(monkeypatch, token):
        import io
        import json
        import urllib.request

        def urlopen(req, timeout=None):
            if token is None:
                raise OSError("identity down")
            return io.BytesIO(json.dumps({"github_token": token}).encode())
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    def test_identity_token_comes_first(self, monkeypatch):
        from services.control_plane import main
        self._identity(monkeypatch, "ghp_identity")
        monkeypatch.setenv("GHCR_TOKEN", "ghcr_env")
        monkeypatch.setenv("GITHUB_TOKEN", "gh_env")
        assert main._github_token() == "ghp_identity"

    def test_ghcr_token_before_github_token(self, monkeypatch):
        from services.control_plane import main
        self._identity(monkeypatch, None)
        monkeypatch.setenv("GHCR_TOKEN", "ghcr_env")
        monkeypatch.setenv("GITHUB_TOKEN", "gh_env")
        assert main._github_token() == "ghcr_env"

    def test_falls_back_to_github_token(self, monkeypatch):
        from services.control_plane import main
        self._identity(monkeypatch, "")
        monkeypatch.delenv("GHCR_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "gh_env")
        assert main._github_token() == "gh_env"


class TestEnvironmentVariableConfiguration:
    """Test that environment variables are properly configured."""

    def test_ghcr_token_in_docker_compose(self):
        """GHCR_TOKEN should be in docker-compose.yml."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "docker-compose.yml"),
        ) as f:
            compose_content = f.read()
        assert "GHCR_TOKEN" in compose_content

    def test_github_token_in_docker_compose(self):
        """GITHUB_TOKEN should be in docker-compose.yml."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "docker-compose.yml"),
        ) as f:
            compose_content = f.read()
        assert "GITHUB_TOKEN" in compose_content

    def test_ghcr_token_in_env_file(self):
        """GHCR_TOKEN should be declared in the shipped env template.

        `.env` is gitignored and therefore absent in CI, so asserting against
        it only ever passed on a developer machine. `.env.example` is the
        committed contract that actually reaches a fresh deploy.
        """
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env.example"),
        ) as f:
            env_content = f.read()
        assert "GHCR_TOKEN" in env_content

    def test_github_token_in_env_file(self):
        """GITHUB_TOKEN should be declared in the shipped env template."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env.example"),
        ) as f:
            env_content = f.read()
        assert "GITHUB_TOKEN" in env_content


class TestDocumentation:
    """Test that documentation is updated."""

    def test_control_plane_docs_mention_fallback(self):
        """Documentation should mention GHCR_TOKEN fallback to GITHUB_TOKEN."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "CONTROL_PLANE_SERVICE.md"),
        ) as f:
            docs_content = f.read()
        assert "GHCR_TOKEN" in docs_content
        assert "GITHUB_TOKEN" in docs_content or "fallback" in docs_content.lower()
