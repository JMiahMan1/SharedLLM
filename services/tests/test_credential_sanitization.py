# services/tests/test_credential_sanitization.py
"""Tests to ensure credentials are never leaked to LLM or logs."""
from services.gateway.agent_loop import sanitize_for_llm


class TestSanitizeForLLM:
    """Credential sanitization must prevent any secret from reaching the LLM."""

    def test_redacts_known_credential_keys(self):
        obj = {
            "user": "default",
            "ha_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake",
            "nextcloud_pass": "Summers@2006",
            "api_key": "e6ec93710dcc4c0db72ac17463401aeece3db95310fae5ee",
            "github_token": "ghp_xxxx",
            "is_admin": True,
        }
        result = sanitize_for_llm(obj)
        assert result["ha_token"] == "[REDACTED]"
        assert result["nextcloud_pass"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["github_token"] == "[REDACTED]"
        assert result["user"] == "default"
        assert result["is_admin"] is True

    def test_redacts_nested_credentials(self):
        obj = {
            "detail": [
                {
                    "loc": ["body", "user_context"],
                    "input": {
                        "user": "default",
                        "ha_token": "secret-token-123",
                        "nextcloud_pass": "my-password",
                    },
                }
            ]
        }
        result = sanitize_for_llm(obj)
        inner = result["detail"][0]["input"]
        assert inner["ha_token"] == "[REDACTED]"
        assert inner["nextcloud_pass"] == "[REDACTED]"
        assert inner["user"] == "default"

    def test_redacts_bearer_token_in_string(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123"
        result = sanitize_for_llm(text)
        assert "eyJhbGci" not in result
        assert "[REDACTED]" in result

    def test_redacts_ghp_token_in_string(self):
        text = "using ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12"
        result = sanitize_for_llm(text)
        assert "ghp_ABCD" not in result
        assert "[REDACTED]" in result

    def test_redacts_github_pat_in_string(self):
        text = "token: github_pat_11ABCD1234567890abcdef"
        result = sanitize_for_llm(text)
        assert "github_pat_" not in result
        assert "[REDACTED]" in result

    def test_redacts_glpat_token_in_string(self):
        text = "GitLab token: glpat-ABCDEFGHIJKLMNOPQRST"
        result = sanitize_for_llm(text)
        assert "glpat-" not in result
        assert "[REDACTED]" in result

    def test_preserves_non_sensitive_data(self):
        obj = {
            "status": "SUCCESS",
            "message": "File saved successfully",
            "path": "/workspaces/system/sharedllm/test.py",
            "line": 42,
        }
        result = sanitize_for_llm(obj)
        assert result == obj

    def test_deep_nesting_limit(self):
        obj = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": {"k": "deep"}}}}}}}}}}}
        result = sanitize_for_llm(obj)
        k_val = result["a"]["b"]["c"]["d"]["e"]["f"]["g"]["h"]["i"]["j"]["k"]
        assert k_val == "[REDACTED]"

    def test_422_error_response_sanitization(self):
        """Simulate a 422 response that echoes back the full payload with credentials."""
        error_detail = [
            {
                "type": "missing",
                "loc": ["body", "query"],
                "msg": "Field required",
                "input": {
                    "search_pattern": "except: ",
                    "user_context": {
                        "user": "default",
                        "ha_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret",
           "nextcloud_pass": "test-password-123",
                        "api_key": "e6ec93710dcc4c0db72ac17463401aeece3db95310fae5ee",
                        "is_admin": True,
                    },
                },
            }
        ]
        result = sanitize_for_llm(error_detail)
        inner = result[0]["input"]["user_context"]
        assert inner["ha_token"] == "[REDACTED]"
        assert inner["nextcloud_pass"] == "[REDACTED]"
        assert inner["api_key"] == "[REDACTED]"
        assert inner["is_admin"] is True

    def test_list_of_dicts_sanitized(self):
        items = [
            {"api_key": "secret123"},
            {"name": "test"},
        ]
        result = sanitize_for_llm(items)
        assert result[0]["api_key"] == "[REDACTED]"
        assert result[1]["name"] == "test"
