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
    """Test token resolution fallback order."""

    def test_ghcr_token_takes_priority_over_github_token(self, control_plane_code):
        """GHCR_TOKEN should be used when both GHCR_TOKEN and GITHUB_TOKEN are set."""
        assert "os.getenv(\"GHCR_TOKEN\", \"\")" in control_plane_code
        assert "os.getenv(\"GITHUB_TOKEN\", \"\")" in control_plane_code

        # Verify GHCR_TOKEN is checked before GITHUB_TOKEN
        ghcr_pos = control_plane_code.find('os.getenv("GHCR_TOKEN", "")')
        github_pos = control_plane_code.find('os.getenv("GITHUB_TOKEN", "")')
        assert ghcr_pos < github_pos, "GHCR_TOKEN should be checked before GITHUB_TOKEN"

    def test_fallback_to_github_token(self, control_plane_code):
        """Should fall back to GITHUB_TOKEN if GHCR_TOKEN is empty."""
        assert "# Fallback to GHCR_TOKEN environment variable" in control_plane_code
        assert "# Fallback to GITHUB_TOKEN environment variable" in control_plane_code

        # Verify the fallback logic structure
        assert 'if not ghcr_token:' in control_plane_code
        assert 'ghcr_token = os.getenv("GHCR_TOKEN", "")' in control_plane_code
        assert 'ghcr_token = os.getenv("GITHUB_TOKEN", "")' in control_plane_code

    def test_identity_service_fetched_first(self, control_plane_code):
        """Should fetch github_token from identity service first."""
        assert "identity_svc_url" in control_plane_code
        assert "/api/resolve" in control_plane_code
        assert '"user_id": 1' in control_plane_code

    def test_github_token_from_identity_takes_priority(self, control_plane_code):
        """Identity service github_token should be used before env vars."""
        # Verify identity service fetch comes before env var fallbacks
        identity_pos = control_plane_code.find('resp_data.get("github_token")')
        ghcr_env_pos = control_plane_code.find('os.getenv("GHCR_TOKEN", "")')
        github_env_pos = control_plane_code.find('os.getenv("GITHUB_TOKEN", "")')

        assert identity_pos < ghcr_env_pos, "Identity service should be checked before GHCR_TOKEN"
        assert identity_pos < github_env_pos, "Identity service should be checked before GITHUB_TOKEN"


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
        """GHCR_TOKEN should be in .env file."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
        ) as f:
            env_content = f.read()
        assert "GHCR_TOKEN" in env_content

    def test_github_token_in_env_file(self):
        """GITHUB_TOKEN should be in .env file."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
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
