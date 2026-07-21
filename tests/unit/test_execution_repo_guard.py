"""Unit tests for the per-workspace repo-write guardrail (services/execution/handlers/git).

The policy is purely per-workspace: a workspace may ONLY push to its OWN
designated repository (its `repo_url`, or a repo Raven created via
`gh repo create`). There is no hardcoded allow/deny list of specific repos -
SharedLLM stays safe simply because no ordinary workspace is bound to it.
"""


def _load():
    import services.execution.handlers.git as g
    return g


def test_normalize_repo_url_canonicalizes_variants():
    g = _load()
    norm = g.normalize_repo_url
    # https + .git
    assert norm("https://github.com/JMiahMan1/raven-e2e-123.git") == "github.com/jmiahman1/raven-e2e-123"
    # ssh
    assert norm("git@github.com:JMiahMan1/raven-e2e-123.git") == "github.com/jmiahman1/raven-e2e-123"
    # embedded credentials
    assert norm("https://x-access-token:abc@github.com/JMiahMan1/raven-e2e-123.git") == "github.com/jmiahman1/raven-e2e-123"
    # None / empty
    assert norm(None) == ""
    assert norm("") == ""
    assert norm("   ") == ""


def test_push_allowed_when_target_matches_designated_repo():
    g = _load()
    ws = "https://github.com/JMiahMan1/raven-e2e-123.git"
    allowed, reason = g.push_allowed(ws, "git@github.com:JMiahMan1/raven-e2e-123.git")
    assert allowed is True
    assert reason == ""


def test_push_allowed_when_no_designated_repo():
    g = _load()
    allowed, _ = g.push_allowed(None, "https://github.com/JMiahMan1/raven-e2e-123.git")
    assert allowed is True
    allowed2, _ = g.push_allowed("", "https://github.com/JMiahMan1/raven-e2e-123.git")
    assert allowed2 is True


def test_push_blocked_when_target_differs_from_designated_repo():
    g = _load()
    # This is the SharedLLM-from-a-throwaway-workspace case: workspace has its own
    # repo, but the model tries to push to SharedLLM.
    allowed, reason = g.push_allowed(
        "https://github.com/JMiahMan1/raven-e2e-123.git",
        "https://github.com/JMiahMan1/SharedLLM.git",
    )
    assert allowed is False
    assert "designated repository" in reason


def test_push_blocked_when_target_unresolvable():
    g = _load()
    allowed, _ = g.push_allowed("https://github.com/JMiahMan1/raven-e2e-123.git", None)
    assert allowed is False
    allowed2, _ = g.push_allowed("https://github.com/JMiahMan1/raven-e2e-123.git", "")
    assert allowed2 is False
