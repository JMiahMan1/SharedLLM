"""Unit tests for the protected-repo guardrail (services/execution/handlers/repo_guard).

These prove the server blocks pushes to protected repositories (SharedLLM) from
any non-designated workspace, while still allowing:
  * pushes to ordinary created repos (e.g. raven-e2e-<ts>), and
  * pushes from the designated SharedLLM development workspace.
"""
import importlib


def _load():
    import services.execution.handlers.repo_guard as rg
    return rg


def test_is_protected_repo_detects_sharedllm():
    rg = _load()
    assert rg.is_protected_repo("https://github.com/JMiahMan1/SharedLLM.git") is True
    assert rg.is_protected_repo("git@github.com:JMiahMan1/SharedLLM.git") is True
    assert rg.is_protected_repo("https://github.com/JMiahMan1/raven-e2e-123.git") is False
    assert rg.is_protected_repo("") is False
    assert rg.is_protected_repo(None) is False


def test_push_to_protected_blocked_for_regular_workspace():
    rg = _load()
    allowed, reason = rg.push_to_protected_allowed(
        "raven_e2e_python_123", "https://github.com/JMiahMan1/SharedLLM.git"
    )
    assert allowed is False
    assert "BLOCKED" in reason


def test_push_to_protected_allowed_for_dev_workspace():
    rg = _load()
    allowed, _ = rg.push_to_protected_allowed(
        "sharedllm", "https://github.com/JMiahMan1/SharedLLM.git"
    )
    assert allowed is True
    allowed2, _ = rg.push_to_protected_allowed(
        "sharedllm-dev", "https://github.com/JMiahMan1/SharedLLM.git"
    )
    assert allowed2 is True


def test_push_to_unprotected_always_allowed():
    rg = _load()
    for ws in (None, "raven_e2e_x", "sharedllm"):
        allowed, _ = rg.push_to_protected_allowed(
            ws, "https://github.com/JMiahMan1/raven-e2e-999.git"
        )
        assert allowed is True


def test_extract_remote_url_from_command():
    rg = _load()
    assert rg.extract_remote_url_from_command(
        "git remote add origin https://github.com/JMiahMan1/SharedLLM.git"
    ) == "https://github.com/JMiahMan1/SharedLLM.git"
    assert rg.extract_remote_url_from_command(
        "git remote set-url origin git@github.com:JMiahMan1/SharedLLM.git"
    ) == "git@github.com:JMiahMan1/SharedLLM.git"
    # A plain push references the configured origin, so it must be resolved later.
    assert rg.extract_remote_url_from_command("git push -u origin HEAD") is None


def test_env_overrides(monkeypatch):
    rg = _load()
    monkeypatch.setenv("PROTECTED_REPO_PATTERNS", "example/secret")
    monkeypatch.setenv("SHAREDLLM_DEV_WORKSPACE_IDS", "mydev")
    importlib.reload(rg)
    assert rg.is_protected_repo("https://github.com/example/secret.git") is True
    assert rg.is_protected_repo("https://github.com/JMiahMan1/SharedLLM.git") is False
    assert rg.push_to_protected_allowed("mydev", "https://github.com/example/secret.git")[0] is True
    assert rg.push_to_protected_allowed("other", "https://github.com/example/secret.git")[0] is False
    # restore defaults for any later tests in the process
    monkeypatch.delenv("PROTECTED_REPO_PATTERNS", raising=False)
    monkeypatch.delenv("SHAREDLLM_DEV_WORKSPACE_IDS", raising=False)
    importlib.reload(rg)
